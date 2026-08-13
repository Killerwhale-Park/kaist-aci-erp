import json
import logging
from typing import Any

from pydantic import ValidationError
from slack_sdk.errors import SlackApiError

from app.config.settings import Settings
from app.domain.catalog import (
    budget_by_id,
    budget_node_by_id,
    budget_nodes,
    budgets,
    categories,
    category_by_id,
    category_for_budget_node,
    department_by_id,
    departments,
)
from app.domain.enums import (
    ApplicantType,
    EvidenceTiming,
    RequestStatus,
    UserRole,
    WorkRequestKind,
    WorkRequestStatus,
)
from app.domain.models import ApprovalRule, ApprovalRuleStep, ExpenseRequest, UserProfile
from app.domain.work_requests import purchase_created_data, settlement_created_data
from app.domain.workflow import (
    APPROVAL_STEP_APPROVED,
    CHANGES_REQUESTED,
    POST_EVIDENCE_SUBMITTED,
    REQUEST_REJECTED,
    REQUEST_RESUBMITTED,
    assert_actor_can_approve,
    created_event_data,
    editable_event_data,
    post_evidence_event_data,
)
from app.exceptions import (
    ApprovalPermissionError,
    ConfigurationError,
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from app.expenses.evidence import drive_warning_urls
from app.expenses.schemas import (
    CreateExpenseCommand,
    EditExpenseCommand,
    EvidenceInput,
    PostEvidenceCommand,
)
from app.i18n import t
from app.ledger import SlackLedgerRepository
from app.slack.home import app_home_view
from app.slack.messages import (
    request_fallback_text,
    request_message_blocks,
    work_request_blocks,
    work_request_fallback_text,
)
from app.slack.modals import (
    administration_modal,
    approval_decision_modal,
    approval_rule_editor_modal,
    approval_rule_selector_modal,
    edit_expense_modal,
    expense_context_modal,
    expense_details_modal,
    post_evidence_modal,
    purchase_request_modal,
    request_details_modal,
    settlement_request_modal,
    system_admins_modal,
)
from app.slack.utils import state_selected_users, state_value
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand

logger = logging.getLogger(__name__)


def register_handlers(slack_app, settings: Settings) -> None:
    def repository(client) -> SlackLedgerRepository:
        return SlackLedgerRepository(client, settings)

    async def open_new_request(
        client,
        trigger_id: str,
        slack_user_id: str,
        initial_department_id: str | None = None,
        source_work_request_id: str | None = None,
        selected_budget_node_ids: tuple[str, ...] = (),
    ) -> None:
        view = expense_context_modal(
            slack_user_id,
            departments(),
            budget_nodes(),
            category_node_ids=(item.id for item in categories()),
            initial_department_id=initial_department_id,
            source_work_request_id=source_work_request_id,
            selected_budget_node_ids=selected_budget_node_ids,
        )
        await safe_open_modal(client, trigger_id, view, slack_user_id)

    async def publish_home(client, slack_user_id: str) -> None:
        ledger = repository(client)
        admins = await ledger.system_admin_ids()
        profile = UserProfile(
            slack_user_id=slack_user_id,
            role=(UserRole.SYSTEM_ADMIN if slack_user_id in admins else UserRole.REQUESTER),
        )
        view = app_home_view(
            profile,
            budgets(),
            await ledger.list_for_applicant(slack_user_id),
            await ledger.list_pending_for_actor(slack_user_id),
            slack_user_id in await ledger.settlement_assigner_ids(),
        )
        await client.views_publish(user_id=slack_user_id, view=view)

    async def synchronize_request_message(client, request: ExpenseRequest) -> ExpenseRequest:
        ledger = repository(client)
        text = request_fallback_text(request)
        await ledger.update_request_view(
            request,
            text=text,
            blocks=request_message_blocks(request),
        )
        return request

    async def synchronize_work_request_message(client, request):
        await repository(client).update_work_request_view(
            request,
            text=work_request_fallback_text(request),
            blocks=work_request_blocks(request),
        )
        return request

    async def safe_dm(
        client, slack_user_id: str, text: str, blocks: list[dict] | None = None
    ) -> None:
        try:
            await client.chat_postMessage(channel=slack_user_id, text=text, blocks=blocks)
        except SlackApiError:
            logger.exception("Failed to send Slack DM to %s", slack_user_id)

    async def safe_open_modal(client, trigger_id: str, view: dict, slack_user_id: str) -> bool:
        try:
            await client.views_open(trigger_id=trigger_id, view=view)
            return True
        except SlackApiError:
            logger.exception("Slack rejected modal %s", view.get("callback_id"))
            await safe_dm(client, slack_user_id, t("form_open_error"))
            return False

    async def send_ephemeral(client, body: dict, text: str, respond=None) -> None:
        try:
            if respond is not None:
                await respond(text=text, response_type="ephemeral", replace_original=False)
                return
            channel_id = body.get("channel", {}).get("id") or body.get("container", {}).get(
                "channel_id"
            )
            if channel_id:
                await client.chat_postEphemeral(
                    channel=channel_id, user=body["user"]["id"], text=text
                )
            else:
                await safe_dm(client, body["user"]["id"], text)
        except SlackApiError:
            logger.exception("Failed to send an ephemeral response")

    async def notify_after_transition(
        client, request: ExpenseRequest, reason: str | None = None
    ) -> None:
        applicant = request.applicant_slack_user_id
        reference = request.reference_number
        if request.status == RequestStatus.CHANGES_REQUESTED:
            text = t("changes_requested_notice", reference=reference, reason=reason or "-")
            await safe_dm(
                client,
                applicant,
                text,
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "edit_request",
                                "style": "primary",
                                "text": {"type": "plain_text", "text": t("edit_request")},
                                "value": request.id,
                            }
                        ],
                    },
                ],
            )
            return
        if request.status == RequestStatus.REJECTED:
            await safe_dm(
                client,
                applicant,
                t("rejected_notice", reference=reference, reason=reason or "-"),
            )
            return
        if request.status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE:
            text = t("post_evidence_needed", reference=reference)
            await safe_dm(
                client,
                applicant,
                text,
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "add_post_evidence",
                                "style": "primary",
                                "text": {
                                    "type": "plain_text",
                                    "text": t("submit_post_evidence"),
                                },
                                "value": request.id,
                            }
                        ],
                    },
                ],
            )
            return
        if request.status == RequestStatus.COMPLETED:
            await safe_dm(client, applicant, t("completed_notice", reference=reference))
            return
        await safe_dm(client, applicant, t("request_updated", reference=reference))
        current = next(
            step for step in request.approval_steps if step.step_order == request.current_step_order
        )
        for approver in current.approvers:
            await safe_dm(
                client, approver.slack_user_id, t("next_review_notice", reference=reference)
            )

    def evidence_from_state(state: dict[str, Any]) -> dict[str, EvidenceInput]:
        evidence: dict[str, EvidenceInput] = {}
        for block_id in state.get("values", {}):
            if not block_id.startswith("evidence__"):
                continue
            key = block_id.removeprefix("evidence__")
            evidence[key] = EvidenceInput(
                url=state_value(state, block_id),
                note=state_value(state, f"note__{key}"),
            )
        return evidence

    def selected_budget_node_ids(state: dict[str, Any]) -> tuple[str, ...]:
        levels = sorted(
            (
                int(block_id.removeprefix("budget_level_")),
                block_id,
            )
            for block_id in state.get("values", {})
            if block_id.startswith("budget_level_")
        )
        return tuple(
            value
            for _, block_id in levels
            if (value := state_value(state, block_id, "budget_node_selected"))
        )

    def expense_context_from_state(
        body: dict,
        *,
        selected_path: tuple[str, ...] | None = None,
        selected_applicant_type: ApplicantType | None = None,
    ) -> dict:
        state = body["view"]["state"]
        metadata = json.loads(body["view"].get("private_metadata") or "{}")
        applicant_type_value = state_value(state, "applicant_type", "applicant_type_changed")
        return expense_context_modal(
            body["user"]["id"],
            departments(),
            budget_nodes(),
            category_node_ids=(item.id for item in categories()),
            initial_department_id=state_value(state, "department"),
            source_work_request_id=metadata.get("source_work_request_id"),
            selected_budget_node_ids=(
                selected_path if selected_path is not None else selected_budget_node_ids(state)
            ),
            applicant_type=(
                selected_applicant_type
                or ApplicantType(applicant_type_value or ApplicantType.STUDENT.value)
            ),
        )

    def editable_command(state: dict[str, Any]) -> EditExpenseCommand:
        return EditExpenseCommand(
            amount=state_value(state, "amount"),
            vendor=state_value(state, "vendor"),
            payment_date=state_value(state, "payment_date"),
            purpose=state_value(state, "purpose"),
            evidence_folder_url=state_value(state, "evidence_folder"),
            evidence=evidence_from_state(state),
        )

    def approval_step_drafts(state: dict[str, Any]) -> list[dict]:
        indexes = sorted(
            int(block_id.removeprefix("step_name_en__"))
            for block_id in state.get("values", {})
            if block_id.startswith("step_name_en__")
        )
        return [
            {
                "name_en": state_value(state, f"step_name_en__{index}") or "",
                "name_ko": state_value(state, f"step_name_ko__{index}") or "",
                "approver_slack_user_ids": state_selected_users(state, f"step_approvers__{index}"),
            }
            for index in indexes
        ]

    async def approval_channel_accepts_configuration(
        client, channel_id: str, steps: list[dict]
    ) -> bool:
        try:
            channel = (await client.conversations_info(channel=channel_id))["channel"]
            if not channel.get("is_private") or not channel.get("is_member"):
                return False
            member_ids: set[str] = set()
            cursor: str | None = None
            while True:
                response = await client.conversations_members(
                    channel=channel_id,
                    limit=200,
                    **({"cursor": cursor} if cursor else {}),
                )
                member_ids.update(response.get("members", []))
                cursor = response.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break
            selected = {user_id for step in steps for user_id in step["approver_slack_user_ids"]}
            return selected.issubset(member_ids)
        except SlackApiError:
            logger.exception("Failed to validate approval channel membership")
            return False

    def pydantic_errors(error: ValidationError) -> dict[str, str]:
        errors: dict[str, str] = {}
        for issue in error.errors():
            field = str(issue["loc"][0])
            block_id = "evidence_folder" if field == "evidence_folder_url" else field
            errors[block_id] = t("amount_invalid") if field == "amount" else t("validation_error")
        return errors or {"purpose": t("validation_error")}

    def domain_errors(error: DomainValidationError) -> dict[str, str]:
        return error.field_errors or {"purpose": t("validation_error")}

    def rule_from_context(context: dict[str, Any]) -> ApprovalRule:
        stored = context["approval_rule"]
        return ApprovalRule(
            department_id=stored["department_id"],
            budget_program_id=stored["budget_program_id"],
            category_id=stored["category_id"],
            approval_channel_id=stored["approval_channel_id"],
            version=int(stored["version"]),
            steps=tuple(
                ApprovalRuleStep(
                    name_en=item["name_en"],
                    name_ko=item["name_ko"],
                    approver_slack_user_ids=tuple(item["approver_slack_user_ids"]),
                )
                for item in stored["steps"]
            ),
        )

    def rule_as_context(rule: ApprovalRule) -> dict[str, Any]:
        return {
            "department_id": rule.department_id,
            "budget_program_id": rule.budget_program_id,
            "category_id": rule.category_id,
            "approval_channel_id": rule.approval_channel_id,
            "version": rule.version,
            "steps": [
                {
                    "name_en": item.name_en,
                    "name_ko": item.name_ko,
                    "approver_slack_user_ids": list(item.approver_slack_user_ids),
                }
                for item in rule.steps
            ],
        }

    async def open_owned_request_modal(
        client, trigger_id: str, request_id: str, actor: str, mode: str
    ) -> None:
        ledger = repository(client)
        request = await ledger.get_request(request_id)
        admins = await ledger.system_admin_ids()
        can_view = (
            request.applicant_slack_user_id == actor
            or actor in admins
            or any(
                actor == item.slack_user_id
                for step in request.approval_steps
                for item in step.approvers
            )
        )
        if not can_view:
            raise ApprovalPermissionError
        if mode in {"edit", "post"} and request.applicant_slack_user_id != actor:
            raise ApprovalPermissionError
        if mode == "edit":
            if request.status != RequestStatus.CHANGES_REQUESTED:
                raise InvalidStateTransitionError
            view = edit_expense_modal(request)
        elif mode == "post":
            has_missing = any(
                item.timing == EvidenceTiming.POST and not item.url
                for item in request.evidence_submissions
            )
            if (
                request.status
                not in {
                    RequestStatus.APPROVED_PENDING_POST_EVIDENCE,
                    RequestStatus.COMPLETED,
                }
                or not has_missing
            ):
                raise InvalidStateTransitionError
            view = post_evidence_modal(request)
        else:
            view = request_details_modal(request)
        await safe_open_modal(client, trigger_id, view, actor)

    @slack_app.command("/expense")
    async def expense_command(ack, body, client):
        await ack()
        await open_new_request(client, body["trigger_id"], body["user_id"])

    @slack_app.event("app_home_opened")
    async def app_home_opened(event, client):
        if event.get("tab") == "home":
            await publish_home(client, event["user"])

    @slack_app.action("new_expense_request")
    async def new_expense_request(ack, body, client):
        await ack()
        selected_id = body["actions"][0].get("value")
        selected_path = (
            (selected_id,) if selected_id and budget_node_by_id(selected_id) is not None else ()
        )
        await open_new_request(
            client,
            body["trigger_id"],
            body["user"]["id"],
            selected_budget_node_ids=selected_path,
        )

    @slack_app.action("new_purchase_work_request")
    async def new_purchase_work_request(ack, body, client):
        await ack()
        await safe_open_modal(
            client,
            body["trigger_id"],
            purchase_request_modal(departments()),
            body["user"]["id"],
        )

    @slack_app.action("new_settlement_work_request")
    async def new_settlement_work_request(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            await repository(client).assert_can_assign_settlement(actor)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        await safe_open_modal(
            client,
            body["trigger_id"],
            settlement_request_modal(departments()),
            actor,
        )

    @slack_app.view("purchase_request_create")
    async def submit_purchase_request(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        try:
            command = CreatePurchaseRequestCommand(
                requester_slack_user_id=actor,
                department_id=state_value(state, "work_department"),
                assignee_slack_user_id=state_value(state, "purchase_assignee"),
                channel_id=state_value(state, "work_channel"),
                item_name=state_value(state, "item_name"),
                product_url=state_value(state, "product_url"),
                quantity=state_value(state, "quantity"),
                estimated_amount=state_value(state, "estimated_amount"),
                purpose=state_value(state, "work_purpose"),
            )
            department = department_by_id(command.department_id)
            if department is None:
                raise ConfigurationError
        except ValidationError as error:
            field_blocks = {
                "department_id": "work_department",
                "assignee_slack_user_id": "purchase_assignee",
                "channel_id": "work_channel",
                "purpose": "work_purpose",
            }
            errors = {
                field_blocks.get(str(issue["loc"][0]), str(issue["loc"][0])): (
                    t("https_required")
                    if str(issue["loc"][0]) == "product_url"
                    else t("validation_error")
                )
                for issue in error.errors()
            }
            await ack(response_action="errors", errors=errors)
            return
        except ConfigurationError:
            await ack(
                response_action="errors",
                errors={"work_department": t("configuration_error")},
            )
            return
        await ack()
        try:
            ledger = repository(client)
            request = await ledger.create_work_request(purchase_created_data(command, department))
            request = await synchronize_work_request_message(client, request)
        except ConfigurationError:
            await safe_dm(client, actor, t("channel_unavailable"))
            return
        except Exception:
            logger.exception("Failed to create a purchase request")
            await safe_dm(client, actor, t("submission_error"))
            return
        await safe_dm(client, actor, t("purchase_request_sent", reference=request.reference_number))
        await safe_dm(
            client,
            request.assignee_slack_user_id,
            t("purchase_assignment_notice", reference=request.reference_number),
            work_request_blocks(request),
        )

    @slack_app.view("settlement_request_create")
    async def submit_settlement_request(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        try:
            command = CreateSettlementRequestCommand(
                requester_slack_user_id=actor,
                department_id=state_value(state, "work_department"),
                assignee_slack_user_id=state_value(state, "settlement_assignee"),
                channel_id=state_value(state, "work_channel"),
                subject=state_value(state, "work_subject"),
                vendor=state_value(state, "work_vendor"),
                amount=state_value(state, "work_amount"),
                payment_date=state_value(state, "work_payment_date"),
                purpose=state_value(state, "work_purpose"),
                evidence_folder_url=state_value(state, "work_evidence_folder"),
            )
            department = department_by_id(command.department_id)
            if department is None:
                raise ConfigurationError
        except ValidationError as error:
            field_blocks = {
                "department_id": "work_department",
                "assignee_slack_user_id": "settlement_assignee",
                "channel_id": "work_channel",
                "subject": "work_subject",
                "vendor": "work_vendor",
                "amount": "work_amount",
                "payment_date": "work_payment_date",
                "purpose": "work_purpose",
                "evidence_folder_url": "work_evidence_folder",
            }
            errors = {}
            for issue in error.errors():
                field = str(issue["loc"][0])
                errors[field_blocks.get(field, field)] = (
                    t("https_required") if field == "evidence_folder_url" else t("validation_error")
                )
            await ack(response_action="errors", errors=errors)
            return
        except ConfigurationError:
            await ack(
                response_action="errors",
                errors={"work_department": t("configuration_error")},
            )
            return
        await ack()
        try:
            ledger = repository(client)
            await ledger.assert_can_assign_settlement(actor)
            request = await ledger.create_work_request(settlement_created_data(command, department))
            request = await synchronize_work_request_message(client, request)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        except ConfigurationError:
            await safe_dm(client, actor, t("channel_unavailable"))
            return
        except Exception:
            logger.exception("Failed to create a settlement request")
            await safe_dm(client, actor, t("submission_error"))
            return
        await safe_dm(
            client, actor, t("settlement_request_sent", reference=request.reference_number)
        )
        await safe_dm(
            client,
            request.assignee_slack_user_id,
            t("settlement_assignment_notice", reference=request.reference_number),
            work_request_blocks(request),
        )

    @slack_app.action("start_assigned_settlement")
    async def start_assigned_settlement(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            request = await repository(client).get_work_request(body["actions"][0]["value"])
            if actor != request.assignee_slack_user_id:
                raise ApprovalPermissionError
            if request.status != WorkRequestStatus.OPEN:
                raise InvalidStateTransitionError
        except (ApprovalPermissionError, EntityNotFoundError):
            await safe_dm(client, actor, t("unauthorized"))
            return
        except InvalidStateTransitionError:
            await safe_dm(client, actor, t("invalid_state"))
            return
        await open_new_request(
            client,
            body["trigger_id"],
            actor,
            initial_department_id=request.department_id,
            source_work_request_id=request.id,
        )

    @slack_app.action("complete_work_request")
    async def complete_work_request(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            request = await repository(client).complete_work_request(
                body["actions"][0]["value"], actor
            )
            await synchronize_work_request_message(client, request)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        except (EntityNotFoundError, InvalidStateTransitionError):
            await safe_dm(client, actor, t("invalid_state"))
            return
        await safe_dm(
            client, actor, t("work_request_completed", reference=request.reference_number)
        )

    @slack_app.action("applicant_type_changed")
    async def applicant_type_changed(ack, body, client):
        await ack()
        selected_type = ApplicantType(body["actions"][0]["selected_option"]["value"])
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=expense_context_from_state(body, selected_applicant_type=selected_type),
        )

    @slack_app.action("budget_node_selected")
    async def budget_node_selected(ack, body, client):
        await ack()
        action = body["actions"][0]
        level = int(action["block_id"].removeprefix("budget_level_"))
        current_path = selected_budget_node_ids(body["view"]["state"])
        selected_path = current_path[: level - 1] + (action["selected_option"]["value"],)
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=expense_context_from_state(body, selected_path=selected_path),
        )

    @slack_app.view("expense_context")
    async def submit_expense_context(ack, body, client):
        state = body["view"]["state"]
        view_metadata = json.loads(body["view"].get("private_metadata") or "{}")
        applicant_type = ApplicantType(
            state_value(state, "applicant_type", "applicant_type_changed")
        )
        identifier_block = (
            "student_number" if applicant_type == ApplicantType.STUDENT else "employee_number"
        )
        applicant_identifier = state_value(state, identifier_block)
        if not applicant_identifier:
            await ack(
                response_action="errors",
                errors={
                    identifier_block: (
                        t("student_id_required")
                        if applicant_type == ApplicantType.STUDENT
                        else t("employee_id_required")
                    )
                },
            )
            return
        department_id = state_value(state, "department")
        selected_path = selected_budget_node_ids(state)
        leaf = budget_node_by_id(selected_path[-1]) if selected_path else None
        category = category_for_budget_node(leaf.id) if leaf else None
        category_id = category.id if category else None
        budget_id = category.budget_program_id if category else None
        budget = budget_by_id(budget_id)
        budget_error_block = (
            f"budget_level_{len(selected_path)}" if selected_path else "budget_level_1"
        )
        if (
            leaf is None
            or category is None
            or budget is None
            or category.budget_program_id != budget.id
        ):
            await ack(
                response_action="errors",
                errors={budget_error_block: t("configuration_error")},
            )
            return
        ledger = repository(client)
        rule = await ledger.get_rule(department_id, category_id)
        if not rule.is_complete:
            await ack(
                response_action="errors",
                errors={budget_error_block: t("approval_configuration_required")},
            )
            return
        source_work_request = None
        source_work_request_id = view_metadata.get("source_work_request_id")
        if source_work_request_id:
            try:
                source_work_request = await ledger.get_work_request(source_work_request_id)
                if (
                    source_work_request.kind != WorkRequestKind.SETTLEMENT
                    or source_work_request.status != WorkRequestStatus.OPEN
                    or source_work_request.assignee_slack_user_id != body["user"]["id"]
                    or source_work_request.department_id != department_id
                ):
                    raise ApprovalPermissionError
            except (ApprovalPermissionError, EntityNotFoundError):
                await ack(
                    response_action="errors",
                    errors={budget_error_block: t("invalid_state")},
                )
                return
        context = {
            "department_id": department_id,
            "applicant_type": applicant_type.value,
            "applicant_identifier": applicant_identifier,
            "budget_program_id": budget_id,
            "category_id": category_id,
            "budget_node_path": list(selected_path),
            "approval_rule": rule_as_context(rule),
            **(
                {"source_work_request_id": source_work_request_id} if source_work_request_id else {}
            ),
        }
        await ack(
            response_action="update",
            view=expense_details_modal(
                context,
                list(category.evidence_requirements),
                source_work_request,
            ),
        )

    @slack_app.view("expense_details")
    async def submit_expense_details(ack, body, client):
        state = body["view"]["state"]
        actor = body["user"]["id"]
        context = json.loads(body["view"]["private_metadata"])
        try:
            command = CreateExpenseCommand(
                applicant_slack_user_id=actor,
                applicant_display_name=body["user"].get("name") or actor,
                department_id=context["department_id"],
                applicant_type=context["applicant_type"],
                applicant_identifier=context.get("applicant_identifier"),
                budget_program_id=context["budget_program_id"],
                category_id=context["category_id"],
                amount=state_value(state, "amount"),
                vendor=state_value(state, "vendor"),
                payment_date=state_value(state, "payment_date"),
                purpose=state_value(state, "purpose"),
                evidence_folder_url=state_value(state, "evidence_folder"),
                evidence=evidence_from_state(state),
            )
            department = department_by_id(command.department_id)
            budget = budget_by_id(command.budget_program_id)
            category = category_by_id(command.category_id)
            if department is None or budget is None or category is None:
                raise ConfigurationError
            created = created_event_data(
                command,
                rule_from_context(context),
                department=department,
                budget=budget,
                category=category,
            )
            await ack()
        except ValidationError as error:
            await ack(response_action="errors", errors=pydantic_errors(error))
            return
        except DomainValidationError as error:
            await ack(response_action="errors", errors=domain_errors(error))
            return
        except ConfigurationError:
            await ack(response_action="errors", errors={"purpose": t("configuration_error")})
            return

        try:
            ledger = repository(client)
            request = await ledger.create_request(created)
            request = await synchronize_request_message(client, request)
            confirmation = t("request_submitted", reference=request.reference_number)
            warning_links = drive_warning_urls(
                [command.evidence_folder_url] + [item.url for item in command.evidence.values()]
            )
            if warning_links:
                confirmation += f"\n\n{t('non_drive_warning')}"
            await safe_dm(client, actor, confirmation)
            await notify_after_transition(client, request)
            source_work_request_id = context.get("source_work_request_id")
            if source_work_request_id:
                try:
                    work_request = await ledger.complete_work_request(source_work_request_id, actor)
                    await synchronize_work_request_message(client, work_request)
                except (ApprovalPermissionError, InvalidStateTransitionError, EntityNotFoundError):
                    logger.exception("Expense submitted but assignment completion failed")
            await publish_home(client, actor)
        except Exception:
            logger.exception("Failed to create an expense ledger record")
            await safe_dm(client, actor, t("submission_error"))

    @slack_app.action("approve_request")
    async def approve_request(ack, body, client, respond):
        await ack()
        request_id = body["actions"][0]["value"]
        actor = body["user"]["id"]
        try:
            request = await repository(client).append_event(
                request_id, APPROVAL_STEP_APPROVED, actor
            )
        except ApprovalPermissionError:
            await send_ephemeral(client, body, t("unauthorized"), respond)
            return
        except InvalidStateTransitionError:
            await send_ephemeral(client, body, t("invalid_state"), respond)
            return
        request = await synchronize_request_message(client, request)
        await notify_after_transition(client, request)
        await publish_home(client, actor)

    async def open_decision(ack, body, client, respond, decision: str) -> None:
        await ack()
        request_id = body["actions"][0]["value"]
        actor = body["user"]["id"]
        try:
            assert_actor_can_approve(await repository(client).get_request(request_id), actor)
        except (ApprovalPermissionError, InvalidStateTransitionError):
            await send_ephemeral(client, body, t("unauthorized"), respond)
            return
        await safe_open_modal(
            client,
            body["trigger_id"],
            approval_decision_modal(request_id, decision),
            actor,
        )

    @slack_app.action("request_changes")
    async def request_changes_action(ack, body, client, respond):
        await open_decision(ack, body, client, respond, "changes")

    @slack_app.action("reject_request")
    async def reject_request_action(ack, body, client, respond):
        await open_decision(ack, body, client, respond, "reject")

    @slack_app.view("approval_decision")
    async def submit_approval_decision(ack, body, client):
        metadata = json.loads(body["view"]["private_metadata"])
        reason = state_value(body["view"]["state"], "decision_reason") or ""
        actor = body["user"]["id"]
        kind = CHANGES_REQUESTED if metadata["decision"] == "changes" else REQUEST_REJECTED
        try:
            request = await repository(client).append_event(
                metadata["request_id"], kind, actor, {"reason": reason}
            )
            await ack()
        except DomainValidationError:
            await ack(response_action="errors", errors={"decision_reason": t("reason_required")})
            return
        except ApprovalPermissionError:
            await ack(response_action="errors", errors={"decision_reason": t("unauthorized")})
            return
        except InvalidStateTransitionError:
            await ack(response_action="errors", errors={"decision_reason": t("invalid_state")})
            return
        request = await synchronize_request_message(client, request)
        await notify_after_transition(client, request, reason)
        await publish_home(client, actor)

    async def open_request_action(ack, body, client, mode: str):
        await ack()
        actor = body["user"]["id"]
        try:
            await open_owned_request_modal(
                client, body["trigger_id"], body["actions"][0]["value"], actor, mode
            )
        except ApprovalPermissionError:
            await safe_dm(
                client, actor, t("not_applicant") if mode != "view" else t("unauthorized")
            )
        except (InvalidStateTransitionError, EntityNotFoundError):
            await safe_dm(client, actor, t("invalid_state"))

    @slack_app.action("view_request")
    async def view_request_action(ack, body, client):
        await open_request_action(ack, body, client, "view")

    @slack_app.action("edit_request")
    async def edit_request_action(ack, body, client):
        await open_request_action(ack, body, client, "edit")

    @slack_app.action("add_post_evidence")
    async def add_post_evidence_action(ack, body, client):
        await open_request_action(ack, body, client, "post")

    @slack_app.view("expense_edit")
    async def submit_expense_edit(ack, body, client):
        metadata = json.loads(body["view"]["private_metadata"])
        actor = body["user"]["id"]
        try:
            command = editable_command(body["view"]["state"])
            request = await repository(client).append_event(
                metadata["request_id"],
                REQUEST_RESUBMITTED,
                actor,
                editable_event_data(command),
            )
            await ack()
        except ValidationError as error:
            await ack(response_action="errors", errors=pydantic_errors(error))
            return
        except DomainValidationError as error:
            await ack(response_action="errors", errors=domain_errors(error))
            return
        except ApprovalPermissionError:
            await ack(response_action="errors", errors={"purpose": t("not_applicant")})
            return
        except InvalidStateTransitionError:
            await ack(response_action="errors", errors={"purpose": t("invalid_state")})
            return
        request = await synchronize_request_message(client, request)
        await notify_after_transition(client, request)
        if drive_warning_urls(
            [command.evidence_folder_url] + [item.url for item in command.evidence.values()]
        ):
            await safe_dm(client, actor, t("non_drive_warning"))
        await publish_home(client, actor)

    @slack_app.view("post_evidence")
    async def submit_post_evidence(ack, body, client):
        metadata = json.loads(body["view"]["private_metadata"])
        actor = body["user"]["id"]
        fallback = next(
            (
                block_id
                for block_id in body["view"]["state"].get("values", {})
                if block_id.startswith("evidence__")
            ),
            "evidence__post_evidence",
        )
        try:
            command = PostEvidenceCommand(evidence=evidence_from_state(body["view"]["state"]))
            request = await repository(client).append_event(
                metadata["request_id"],
                POST_EVIDENCE_SUBMITTED,
                actor,
                post_evidence_event_data(command),
            )
            await ack()
        except (ValidationError, DomainValidationError):
            await ack(response_action="errors", errors={fallback: t("validation_error")})
            return
        except ApprovalPermissionError:
            await ack(response_action="errors", errors={fallback: t("not_applicant")})
            return
        except InvalidStateTransitionError:
            await ack(response_action="errors", errors={fallback: t("invalid_state")})
            return
        request = await synchronize_request_message(client, request)
        await notify_after_transition(client, request)
        if drive_warning_urls([item.url for item in command.evidence.values()]):
            await safe_dm(client, actor, t("non_drive_warning"))
        await publish_home(client, actor)

    @slack_app.action("manage_rules")
    async def manage_rules_action(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            await repository(client).assert_system_admin(actor)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        await safe_open_modal(client, body["trigger_id"], administration_modal(), actor)

    @slack_app.action("configure_approval_rules")
    async def configure_approval_rules_action(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            await repository(client).assert_system_admin(actor)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        await client.views_push(
            trigger_id=body["trigger_id"],
            view=approval_rule_selector_modal(departments(), categories()),
        )

    @slack_app.view("approval_rule_selector")
    async def submit_approval_rule_selector(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        department_id = state_value(state, "rule_department")
        category_id = state_value(state, "rule_category")
        try:
            ledger = repository(client)
            await ledger.assert_system_admin(actor)
            rule = await ledger.get_rule(department_id, category_id)
            view = approval_rule_editor_modal(
                department_id,
                category_id,
                rule.approval_channel_id,
                [
                    {
                        "name_en": step.name_en,
                        "name_ko": step.name_ko,
                        "approver_slack_user_ids": list(step.approver_slack_user_ids),
                    }
                    for step in rule.steps
                ],
            )
            await ack(response_action="update", view=view)
        except (ApprovalPermissionError, EntityNotFoundError):
            await ack(response_action="errors", errors={"rule_category": t("configuration_error")})

    async def update_approval_step_editor(ack, body, client, operation: str) -> None:
        await ack()
        actor = body["user"]["id"]
        try:
            await repository(client).assert_system_admin(actor)
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        metadata = json.loads(body["view"]["private_metadata"])
        state = body["view"]["state"]
        steps = approval_step_drafts(state)
        if operation == "add" and len(steps) < 20:
            order = len(steps) + 1
            steps.append(
                {
                    "name_en": f"Approval Step {order}",
                    "name_ko": f"승인 단계 {order}",
                    "approver_slack_user_ids": [],
                }
            )
        if operation == "remove" and len(steps) > 1:
            steps.pop(int(body["actions"][0]["value"]))
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=approval_rule_editor_modal(
                metadata["department_id"],
                metadata["category_id"],
                state_value(state, "approval_channel"),
                steps,
            ),
        )

    @slack_app.action("add_approval_step")
    async def add_approval_step_action(ack, body, client):
        await update_approval_step_editor(ack, body, client, "add")

    @slack_app.action("remove_approval_step")
    async def remove_approval_step_action(ack, body, client):
        await update_approval_step_editor(ack, body, client, "remove")

    @slack_app.view("approval_rule_editor")
    async def submit_approval_rule_editor(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        metadata = json.loads(body["view"]["private_metadata"])
        state = body["view"]["state"]
        channel_id = state_value(state, "approval_channel")
        steps = approval_step_drafts(state)
        if not channel_id or not await approval_channel_accepts_configuration(
            client, channel_id, steps
        ):
            await safe_dm(client, actor, t("channel_membership_error"))
            return
        category = category_by_id(metadata["category_id"])
        if category is None:
            await safe_dm(client, actor, t("configuration_error"))
            return
        try:
            await repository(client).save_rule(
                actor,
                ApprovalRule(
                    department_id=metadata["department_id"],
                    budget_program_id=category.budget_program_id,
                    category_id=category.id,
                    approval_channel_id=channel_id,
                    steps=tuple(
                        ApprovalRuleStep(
                            name_en=step["name_en"],
                            name_ko=step["name_ko"],
                            approver_slack_user_ids=tuple(step["approver_slack_user_ids"]),
                        )
                        for step in steps
                    ),
                ),
            )
        except (ApprovalPermissionError, ConfigurationError, EntityNotFoundError):
            await safe_dm(client, actor, t("configuration_error"))
            return
        await safe_dm(
            client,
            actor,
            t("rule_saved_incomplete")
            if any(not step["approver_slack_user_ids"] for step in steps)
            else t("rule_saved"),
        )
        await publish_home(client, actor)

    @slack_app.action("configure_system_admins")
    async def configure_system_admins_action(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        try:
            ledger = repository(client)
            await ledger.assert_system_admin(actor)
            admins = await ledger.system_admin_ids()
        except ApprovalPermissionError:
            await safe_dm(client, actor, t("unauthorized"))
            return
        await client.views_push(
            trigger_id=body["trigger_id"], view=system_admins_modal(sorted(admins))
        )

    @slack_app.view("system_admins_editor")
    async def submit_system_admins_editor(ack, body, client):
        selected = state_selected_users(body["view"]["state"], "system_admins")
        if not selected:
            await ack(response_action="errors", errors={"system_admins": t("admin_required")})
            return
        actor = body["user"]["id"]
        try:
            await repository(client).replace_system_admins(actor, selected)
            await ack()
        except ApprovalPermissionError:
            await ack(response_action="errors", errors={"system_admins": t("unauthorized")})
            return
        except ConfigurationError:
            await ack(response_action="errors", errors={"system_admins": t("configuration_error")})
            return
        await safe_dm(client, actor, t("admins_saved"))
        await publish_home(client, actor)
