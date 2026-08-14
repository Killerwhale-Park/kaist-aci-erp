import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError
from slack_sdk.errors import SlackApiError

from app.application.work_items import build_user_work_queue
from app.config.roles import (
    ASSIGN_SETTLEMENT,
    MANAGE_CONFIGURATION,
    SUBMIT_REQUEST,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    assigned_users_with_capability,
    role_definitions,
)
from app.config.work_request_policies import work_request_policy
from app.database import Database
from app.domain.catalog import (
    budget_by_id,
    budget_node_by_id,
    budget_nodes,
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
from app.domain.work_requests import (
    WORK_APPROVAL_STEP_APPROVED,
    WORK_REQUEST_REJECTED,
    assert_actor_can_approve_work,
    purchase_created_data,
    settlement_created_data,
)
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
from app.ledger import LedgerRepository
from app.slack.home import HomeCapabilities, app_home_view
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
    configuration_notice_modal,
    edit_expense_modal,
    expense_context_modal,
    expense_details_modal,
    post_evidence_modal,
    purchase_request_modal,
    request_details_modal,
    role_configuration_modal,
    settlement_request_modal,
    system_channels_modal,
    work_request_details_modal,
    work_request_rejection_modal,
)
from app.slack.utils import state_selected_conversations, state_selected_users, state_value
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand

logger = logging.getLogger(__name__)


def register_handlers(slack_app, database: Database) -> None:
    def repository(client) -> LedgerRepository:
        return LedgerRepository(client, database)

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
        (
            assignments,
            submission_configuration,
            own_expenses,
            pending_expense_approvals,
            submitted_work_requests,
            actionable_work_requests,
        ) = await asyncio.gather(
            ledger.role_assignments(),
            ledger.submission_configuration(),
            ledger.list_active_for_applicant(slack_user_id),
            ledger.list_pending_for_actor(slack_user_id),
            ledger.list_active_work_for_user(slack_user_id),
            ledger.list_actionable_work_for_actor(slack_user_id),
        )
        admins = assigned_users_with_capability(assignments, MANAGE_CONFIGURATION)
        settlement_assigners = assigned_users_with_capability(assignments, ASSIGN_SETTLEMENT)
        requesters = assigned_users_with_capability(assignments, SUBMIT_REQUEST)
        purchase_ready, expense_ready = submission_configuration
        profile = UserProfile(
            slack_user_id=slack_user_id,
            role=(UserRole.SYSTEM_ADMIN if slack_user_id in admins else UserRole.REQUESTER),
        )
        work_queue = build_user_work_queue(
            slack_user_id,
            own_expenses=own_expenses,
            pending_expense_approvals=pending_expense_approvals,
            submitted_work_requests=submitted_work_requests,
            actionable_work_requests=actionable_work_requests,
        )
        view = app_home_view(
            profile,
            work_queue,
            HomeCapabilities(
                can_request=slack_user_id in requesters,
                expense_ready=expense_ready,
                purchase_ready=purchase_ready,
                can_assign_settlement=slack_user_id in settlement_assigners,
            ),
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

    async def safe_alert(client, text: str) -> None:
        try:
            await repository(client).report_alert(text)
        except Exception:
            logger.exception("Failed to publish a system alert")

    async def modal_failure_dm(client, slack_user_id: str, error_code: str) -> None:
        message_key = (
            "form_open_expired"
            if error_code
            in {
                "exchanged_trigger_id",
                "expired_trigger_id",
                "invalid_trigger",
                "invalid_trigger_id",
                "trigger_exchanged",
                "trigger_expired",
            }
            else "form_open_error"
        )
        await safe_dm(client, slack_user_id, f"{t(message_key)}\n`{error_code}`")
        await safe_alert(client, f"Slack modal failure: `{error_code}`")

    async def safe_open_modal(client, trigger_id: str, view: dict, slack_user_id: str):
        try:
            return await client.views_open(trigger_id=trigger_id, view=view)
        except SlackApiError as exc:
            error_code = str(exc.response.get("error") or "unknown_error")
            logger.exception("Slack rejected modal %s: %s", view.get("callback_id"), error_code)
            await modal_failure_dm(client, slack_user_id, error_code)
            return None

    async def safe_push_modal(client, trigger_id: str, view: dict, slack_user_id: str):
        try:
            return await client.views_push(trigger_id=trigger_id, view=view)
        except SlackApiError as exc:
            error_code = str(exc.response.get("error") or "unknown_error")
            logger.exception(
                "Slack rejected pushed modal %s: %s", view.get("callback_id"), error_code
            )
            await modal_failure_dm(client, slack_user_id, error_code)
            return None

    async def send_ephemeral(client, body: dict, text: str, respond=None) -> None:
        try:
            if respond is not None and body.get("response_url"):
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
                                "value": request.slack_locator,
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
                                "value": request.slack_locator,
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
                    approver_roles=tuple(item.get("approver_roles", [])),
                )
                for item in stored["steps"]
            ),
            workflow_id=stored.get("workflow_id"),
            workflow_name_en=stored.get("workflow_name_en"),
            workflow_name_ko=stored.get("workflow_name_ko"),
        )

    def rule_as_context(rule: ApprovalRule) -> dict[str, Any]:
        return {
            "department_id": rule.department_id,
            "budget_program_id": rule.budget_program_id,
            "category_id": rule.category_id,
            "approval_channel_id": rule.approval_channel_id,
            "version": rule.version,
            "workflow_id": rule.workflow_id,
            "workflow_name_en": rule.workflow_name_en,
            "workflow_name_ko": rule.workflow_name_ko,
            "steps": [
                {
                    "name_en": item.name_en,
                    "name_ko": item.name_ko,
                    "approver_slack_user_ids": list(item.approver_slack_user_ids),
                    "approver_roles": list(item.approver_roles),
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
        try:
            await open_new_request(client, body["trigger_id"], body["user_id"])
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, body["user_id"], t("requester_role_required"))
            return
        await ack()

    @slack_app.event("app_home_opened")
    async def app_home_opened(event, client):
        if event.get("tab") == "home":
            await publish_home(client, event["user"])

    @slack_app.action("new_expense_request")
    async def new_expense_request(ack, body, client):
        selected_id = body["actions"][0].get("value")
        selected_path = (
            (selected_id,) if selected_id and budget_node_by_id(selected_id) is not None else ()
        )
        try:
            await open_new_request(
                client,
                body["trigger_id"],
                body["user"]["id"],
                selected_budget_node_ids=selected_path,
            )
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, body["user"]["id"], t("requester_role_required"))
            return
        await ack()

    @slack_app.action("new_purchase_work_request")
    async def new_purchase_work_request(ack, body, client):
        actor = body["user"]["id"]
        await safe_open_modal(
            client,
            body["trigger_id"],
            purchase_request_modal(departments()),
            actor,
        )
        await ack()

    @slack_app.action("new_settlement_work_request")
    async def new_settlement_work_request(ack, body, client):
        actor = body["user"]["id"]
        try:
            await repository(client).assert_can_assign_settlement(actor)
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        await safe_open_modal(
            client,
            body["trigger_id"],
            settlement_request_modal(departments()),
            actor,
        )
        await ack()

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
            ledger = repository(client)
            await ledger.assert_operating_channel(command.channel_id)
            await ledger.assert_can_submit_request(actor, command.channel_id)
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
                errors={"work_channel": t("channel_unavailable")},
            )
            return
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"work_channel": t("requester_role_required")},
            )
            return
        try:
            policy = work_request_policy(WorkRequestKind.PURCHASE)
            if policy.approval_workflow_id is None:
                raise ConfigurationError("Purchase approval workflow is not configured")
            workflow = await ledger.resolve_approval_workflow(
                policy.approval_workflow_id,
                command.channel_id,
                actor_bindings={
                    "payment_assignee": {command.assignee_slack_user_id},
                },
            )
            if not workflow.is_complete:
                await ack(
                    response_action="errors",
                    errors={"purchase_assignee": t("approval_configuration_required")},
                )
                return
            await ack()
        except EntityNotFoundError:
            await ack(
                response_action="errors",
                errors={"purchase_assignee": t("approval_configuration_required")},
            )
            return
        except ConfigurationError:
            await ack(
                response_action="errors",
                errors={"work_channel": t("channel_unavailable")},
            )
            return

        try:
            request = await ledger.create_work_request(
                purchase_created_data(command, department, workflow)
            )
        except Exception as error:
            logger.exception("Failed to create a purchase request")
            await safe_alert(
                client,
                f"Purchase request database write failed ({type(error).__name__})",
            )
            await safe_dm(client, actor, t("submission_error"))
            return

        projection_ok = True
        try:
            request = await synchronize_work_request_message(client, request)
        except Exception as error:
            projection_ok = False
            logger.exception("Purchase request saved but Slack projection failed")
            await safe_alert(
                client,
                f"Purchase request {request.reference_number} Slack projection failed "
                f"({type(error).__name__})",
            )
        await safe_dm(
            client,
            actor,
            t(
                "purchase_request_sent" if projection_ok else "request_saved_projection_failed",
                reference=request.reference_number,
            ),
        )
        await safe_dm(
            client,
            request.assignee_slack_user_id,
            t("purchase_assignment_notice", reference=request.reference_number),
            work_request_blocks(request),
        )
        await publish_home(client, actor)

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
            ledger = repository(client)
            await ledger.assert_operating_channel(command.channel_id)
            await ledger.assert_can_assign_settlement(actor, command.channel_id)
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
                errors={"work_channel": t("channel_unavailable")},
            )
            return
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"work_department": t("unauthorized")},
            )
            return
        await ack()

        try:
            request = await ledger.create_work_request(settlement_created_data(command, department))
        except Exception as error:
            logger.exception("Failed to create a settlement request")
            await safe_alert(
                client,
                f"Settlement request database write failed ({type(error).__name__})",
            )
            await safe_dm(client, actor, t("submission_error"))
            return

        projection_ok = True
        try:
            request = await synchronize_work_request_message(client, request)
        except Exception as error:
            projection_ok = False
            logger.exception("Settlement request saved but Slack projection failed")
            await safe_alert(
                client,
                f"Settlement request {request.reference_number} Slack projection failed "
                f"({type(error).__name__})",
            )
        await safe_dm(
            client,
            actor,
            t(
                "settlement_request_sent" if projection_ok else "request_saved_projection_failed",
                reference=request.reference_number,
            ),
        )
        await safe_dm(
            client,
            request.assignee_slack_user_id,
            t("settlement_assignment_notice", reference=request.reference_number),
            work_request_blocks(request),
        )
        await publish_home(client, actor)

    @slack_app.action("view_work_request")
    async def view_work_request(ack, body, client):
        actor = body["user"]["id"]
        try:
            ledger = repository(client)
            request = await ledger.get_work_request(body["actions"][0]["value"])
            allowed = {
                request.requester_slack_user_id,
                request.originator_slack_user_id,
                request.assignee_slack_user_id,
                *request.current_approver_slack_user_ids,
                *(await ledger.system_admin_ids()),
            }
            if actor not in allowed:
                raise ApprovalPermissionError
            await safe_open_modal(
                client,
                body["trigger_id"],
                work_request_details_modal(request),
                actor,
            )
        except (ApprovalPermissionError, EntityNotFoundError):
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        await ack()

    @slack_app.action("approve_work_request")
    async def approve_work_request(ack, body, client, respond):
        await ack()
        actor = body["user"]["id"]
        try:
            request = await repository(client).append_work_event(
                body["actions"][0]["value"],
                WORK_APPROVAL_STEP_APPROVED,
                actor,
            )
            await synchronize_work_request_message(client, request)
            await publish_home(client, actor)
            if request.status == WorkRequestStatus.ACTION_REQUIRED:
                await safe_dm(
                    client,
                    request.assignee_slack_user_id,
                    t("purchase_payment_ready", reference=request.reference_number),
                    work_request_blocks(request),
                )
        except ApprovalPermissionError:
            await send_ephemeral(client, body, t("unauthorized"), respond)
        except (EntityNotFoundError, InvalidStateTransitionError):
            await send_ephemeral(client, body, t("invalid_state"), respond)

    @slack_app.action("reject_work_request")
    async def reject_work_request(ack, body, client):
        actor = body["user"]["id"]
        try:
            request_id = body["actions"][0]["value"]
            assert_actor_can_approve_work(
                await repository(client).get_work_request(request_id), actor
            )
            await safe_open_modal(
                client,
                body["trigger_id"],
                work_request_rejection_modal(request_id),
                actor,
            )
        except (EntityNotFoundError, InvalidStateTransitionError):
            await ack()
            await safe_dm(client, actor, t("invalid_state"))
            return
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        await ack()

    @slack_app.view("work_request_rejection")
    async def submit_work_request_rejection(ack, body, client):
        actor = body["user"]["id"]
        metadata = json.loads(body["view"]["private_metadata"])
        reason = state_value(body["view"]["state"], "decision_reason") or ""
        if not reason.strip():
            await ack(
                response_action="errors",
                errors={"decision_reason": t("reason_required")},
            )
            return
        try:
            request = await repository(client).append_work_event(
                metadata["request_id"],
                WORK_REQUEST_REJECTED,
                actor,
                {"reason": reason},
            )
            await ack()
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"decision_reason": t("unauthorized")},
            )
            return
        except (EntityNotFoundError, InvalidStateTransitionError):
            await ack(
                response_action="errors",
                errors={"decision_reason": t("invalid_state")},
            )
            return
        await synchronize_work_request_message(client, request)
        await safe_dm(
            client,
            request.requester_slack_user_id,
            t("purchase_rejected", reference=request.reference_number),
        )
        await publish_home(client, actor)

    @slack_app.action("handoff_purchase_to_settlement")
    async def handoff_purchase_to_settlement(ack, body, client):
        actor = body["user"]["id"]
        try:
            request = await repository(client).get_work_request(body["actions"][0]["value"])
            if (
                request.kind != WorkRequestKind.PURCHASE
                or request.status not in {WorkRequestStatus.ACTION_REQUIRED, WorkRequestStatus.OPEN}
                or request.assignee_slack_user_id != actor
            ):
                raise ApprovalPermissionError
            await safe_open_modal(
                client,
                body["trigger_id"],
                settlement_request_modal(departments(), source_purchase=request),
                actor,
            )
        except (ApprovalPermissionError, EntityNotFoundError):
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        await ack()

    @slack_app.view("purchase_settlement_handoff")
    async def submit_purchase_settlement_handoff(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        metadata = json.loads(body["view"]["private_metadata"])
        ledger = repository(client)
        try:
            source = await ledger.get_work_request(metadata["source_request_id"])
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
            if (
                department is None
                or source.kind != WorkRequestKind.PURCHASE
                or source.department_id != command.department_id
                or source.assignee_slack_user_id != actor
            ):
                raise ConfigurationError
            await ledger.assert_operating_channel(command.channel_id)
            await ledger.assert_channel_member(actor, command.channel_id)
            created_data = settlement_created_data(
                command,
                department,
                originator_slack_user_id=source.originator_slack_user_id,
                case_id=source.case_id,
                parent_request_id=source.id,
            )
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
            await ack(
                response_action="errors",
                errors={
                    field_blocks.get(str(issue["loc"][0]), str(issue["loc"][0])): (
                        t("https_required")
                        if str(issue["loc"][0]) == "evidence_folder_url"
                        else t("validation_error")
                    )
                    for issue in error.errors()
                },
            )
            return
        except (ApprovalPermissionError, ConfigurationError, EntityNotFoundError):
            await ack(
                response_action="errors",
                errors={"work_department": t("invalid_state")},
            )
            return
        await ack()
        try:
            source, successor = await ledger.handoff_work_request(source.id, actor, created_data)
        except Exception as error:
            logger.exception("Failed to hand off purchase to settlement")
            await safe_alert(
                client,
                f"Purchase handoff database write failed ({type(error).__name__})",
            )
            await safe_dm(client, actor, t("submission_error"))
            return

        projection_ok = True
        for item in (source, successor):
            try:
                await synchronize_work_request_message(client, item)
            except Exception as error:
                projection_ok = False
                logger.exception("Purchase handoff saved but Slack projection failed")
                await safe_alert(
                    client,
                    f"Work request {item.reference_number} Slack projection failed "
                    f"({type(error).__name__})",
                )
        if not projection_ok:
            await safe_dm(
                client,
                actor,
                t("request_saved_projection_failed", reference=successor.reference_number),
            )
        await safe_dm(
            client,
            successor.assignee_slack_user_id,
            t("settlement_assignment_notice", reference=successor.reference_number),
            work_request_blocks(successor),
        )
        await publish_home(client, actor)

    @slack_app.action("start_assigned_settlement")
    async def start_assigned_settlement(ack, body, client):
        actor = body["user"]["id"]
        try:
            request = await repository(client).get_work_request(body["actions"][0]["value"])
            if actor != request.assignee_slack_user_id:
                raise ApprovalPermissionError
            if request.status not in {
                WorkRequestStatus.ACTION_REQUIRED,
                WorkRequestStatus.OPEN,
            }:
                raise InvalidStateTransitionError
        except (ApprovalPermissionError, EntityNotFoundError):
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        except InvalidStateTransitionError:
            await ack()
            await safe_dm(client, actor, t("invalid_state"))
            return
        await open_new_request(
            client,
            body["trigger_id"],
            actor,
            initial_department_id=request.department_id,
            source_work_request_id=request.slack_locator,
        )
        await ack()

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
        selected_type = ApplicantType(body["actions"][0]["selected_option"]["value"])
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=expense_context_from_state(body, selected_applicant_type=selected_type),
        )
        await ack()

    @slack_app.action("budget_node_selected")
    async def budget_node_selected(ack, body, client):
        action = body["actions"][0]
        level = int(action["block_id"].removeprefix("budget_level_"))
        current_path = selected_budget_node_ids(body["view"]["state"])
        selected_path = current_path[: level - 1] + (action["selected_option"]["value"],)
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=expense_context_from_state(body, selected_path=selected_path),
        )
        await ack()

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
        category = category_for_budget_node(leaf.id, department_id) if leaf else None
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
        try:
            rule = await ledger.get_rule(department_id, category_id)
            if not rule.approval_channel_id:
                raise ConfigurationError
            await ledger.assert_can_submit_request(body["user"]["id"], rule.approval_channel_id)
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"department": t("requester_role_required")},
            )
            return
        except (ConfigurationError, EntityNotFoundError):
            await ack(
                response_action="errors",
                errors={budget_error_block: t("approval_configuration_required")},
            )
            return
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
                    or source_work_request.status
                    not in {WorkRequestStatus.ACTION_REQUIRED, WorkRequestStatus.OPEN}
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
                {
                    "source_work_request_id": source_work_request_id,
                    "case_id": source_work_request.case_id,
                }
                if source_work_request_id and source_work_request
                else {}
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
            category = category_by_id(command.category_id, command.department_id)
            if department is None or budget is None or category is None:
                raise ConfigurationError
            created = created_event_data(
                command,
                rule_from_context(context),
                department=department,
                budget=budget,
                category=category,
            )
            if context.get("source_work_request_id"):
                created["source_work_request_id"] = context["source_work_request_id"]
                created["case_id"] = context.get("case_id")
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

        ledger = repository(client)
        try:
            request = await ledger.create_request(created)
        except Exception as error:
            logger.exception("Failed to create an expense ledger record")
            await safe_alert(
                client,
                f"Expense request database write failed ({type(error).__name__})",
            )
            await safe_dm(client, actor, t("submission_error"))
            return

        projection_ok = True
        try:
            request = await synchronize_request_message(client, request)
        except Exception as error:
            projection_ok = False
            logger.exception("Expense request saved but Slack projection failed")
            await safe_alert(
                client,
                f"Expense request {request.reference_number} Slack projection failed "
                f"({type(error).__name__})",
            )

        confirmation = t(
            "request_submitted" if projection_ok else "request_saved_projection_failed",
            reference=request.reference_number,
        )
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
                work_request = await ledger.complete_work_request(
                    source_work_request_id,
                    actor,
                    successor_type="EXPENSE_REQUEST",
                    successor_id=request.id,
                )
                await synchronize_work_request_message(client, work_request)
            except Exception as error:
                logger.exception("Expense submitted but assignment completion failed")
                await safe_alert(
                    client,
                    f"Expense request {request.reference_number} assignment completion failed "
                    f"({type(error).__name__})",
                )
        await publish_home(client, actor)

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
        request_id = body["actions"][0]["value"]
        actor = body["user"]["id"]
        try:
            assert_actor_can_approve(await repository(client).get_request(request_id), actor)
        except (ApprovalPermissionError, InvalidStateTransitionError):
            await ack()
            await send_ephemeral(client, body, t("unauthorized"), respond)
            return
        await safe_open_modal(
            client,
            body["trigger_id"],
            approval_decision_modal(request_id, decision),
            actor,
        )
        await ack()

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
        actor = body["user"]["id"]
        try:
            await open_owned_request_modal(
                client, body["trigger_id"], body["actions"][0]["value"], actor, mode
            )
        except ApprovalPermissionError:
            await ack()
            await safe_dm(
                client, actor, t("not_applicant") if mode != "view" else t("unauthorized")
            )
            return
        except (InvalidStateTransitionError, EntityNotFoundError):
            await ack()
            await safe_dm(client, actor, t("invalid_state"))
            return
        await ack()

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
        actor = body["user"]["id"]
        opened = await safe_open_modal(
            client,
            body["trigger_id"],
            administration_modal(),
            actor,
        )
        await ack()
        if opened is None:
            return

    @slack_app.action("configure_system_channels")
    async def configure_system_channels_action(ack, body, client):
        actor = body["user"]["id"]
        try:
            ledger = repository(client)
            await ledger.assert_system_admin(actor)
            channel_configuration = await ledger.system_channels()
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        except (ConfigurationError, SlackApiError):
            logger.exception("Failed to load system channel configuration")
            await ack()
            await safe_dm(client, actor, t("configuration_load_error"))
            return
        await safe_push_modal(
            client,
            body["trigger_id"],
            system_channels_modal(channel_configuration),
            actor,
        )
        await ack()

    @slack_app.view("system_channels_editor")
    async def submit_system_channels_editor(ack, body, client):
        state = body["view"]["state"]
        audit_channel_id = state_value(state, "audit_channel")
        alerts_channel_id = state_value(state, "alerts_channel")
        additional = state_selected_conversations(state, "additional_operating_channels")
        actor = body["user"]["id"]
        try:
            await repository(client).replace_system_channels(
                actor,
                audit_channel_id=audit_channel_id or "",
                alerts_channel_id=alerts_channel_id or "",
                additional_operating_channel_ids=additional,
            )
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"audit_channel": t("unauthorized")},
            )
            return
        except ConfigurationError:
            await ack(
                response_action="errors",
                errors={"audit_channel": t("system_channels_error")},
            )
            return
        await ack()
        await safe_dm(client, actor, t("system_channels_saved"))
        await publish_home(client, actor)

    @slack_app.action("configure_approval_rules")
    async def configure_approval_rules_action(ack, body, client):
        actor = body["user"]["id"]
        pushed = await safe_push_modal(
            client,
            body["trigger_id"],
            approval_rule_selector_modal(departments(), categories()),
            actor,
        )
        await ack()
        if pushed is None:
            return

    @slack_app.view("approval_rule_selector")
    async def submit_approval_rule_selector(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        department_id = state_value(state, "rule_department")
        category_id = state_value(state, "rule_category")
        if not department_id or not category_id:
            await ack(response_action="errors", errors={"rule_category": t("validation_error")})
            return
        try:
            ledger = repository(client)
            await ledger.assert_system_admin(actor)
            rule = await ledger.get_rule(department_id, category_id)
        except ApprovalPermissionError:
            await ack(response_action="update", view=configuration_notice_modal(t("unauthorized")))
            return
        except EntityNotFoundError:
            await ack(
                response_action="update",
                view=configuration_notice_modal(t("approval_configuration_required")),
            )
            return
        except (ConfigurationError, SlackApiError):
            logger.exception("Failed to load approval rule %s:%s", department_id, category_id)
            await ack(
                response_action="update",
                view=configuration_notice_modal(t("configuration_load_error")),
            )
            return

        await ack(
            response_action="update",
            view=approval_rule_editor_modal(
                department_id,
                category_id,
                rule.approval_channel_id if rule else None,
                rule.workflow_name_en if rule and rule.workflow_name_en else "Approval Workflow",
                rule.workflow_name_ko if rule and rule.workflow_name_ko else "승인 절차",
                (
                    [
                        {
                            "name_en": step.name_en,
                            "name_ko": step.name_ko,
                            "approver_slack_user_ids": list(step.approver_slack_user_ids),
                            "approver_roles": list(step.approver_roles),
                        }
                        for step in rule.steps
                    ]
                    if rule
                    else []
                ),
            ),
        )

    @slack_app.view("approval_rule_editor")
    async def submit_approval_rule_editor(ack, body, client):
        actor = body["user"]["id"]
        metadata = json.loads(body["view"]["private_metadata"])
        state = body["view"]["state"]
        channel_id = state_value(state, "approval_channel")
        if not channel_id:
            await ack(
                response_action="errors",
                errors={"approval_channel": t("channel_membership_error")},
            )
            return
        try:
            saved = await repository(client).save_approval_route(
                actor,
                metadata["department_id"],
                metadata["category_id"],
                channel_id,
            )
        except (ApprovalPermissionError, ConfigurationError, EntityNotFoundError):
            await ack(
                response_action="errors",
                errors={"approval_channel": t("configuration_error")},
            )
            return
        await ack()
        await safe_dm(
            client,
            actor,
            t("rule_saved_incomplete") if not saved.is_complete else t("rule_saved"),
        )
        await publish_home(client, actor)

    @slack_app.action("configure_access_roles")
    async def configure_access_roles_action(ack, body, client):
        actor = body["user"]["id"]
        try:
            ledger = repository(client)
            await ledger.assert_system_admin(actor)
            assignments = await ledger.role_assignments()
        except ApprovalPermissionError:
            await ack()
            await safe_dm(client, actor, t("unauthorized"))
            return
        except (ConfigurationError, SlackApiError):
            logger.exception("Failed to load access role configuration")
            await ack()
            await safe_dm(client, actor, t("configuration_load_error"))
            return
        await safe_push_modal(
            client,
            body["trigger_id"],
            role_configuration_modal(assignments),
            actor,
        )
        await ack()

    @slack_app.view("access_roles_editor")
    async def submit_access_roles_editor(ack, body, client):
        state = body["view"]["state"]
        assignments = {
            WORKSPACE_ROLE_SCOPE: {
                role.id: set(
                    state_selected_users(
                        state,
                        f"access_role__{WORKSPACE_ROLE_SCOPE}__{role.id}",
                    )
                )
                for role in role_definitions()
            }
        }
        if not assignments[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]:
            await ack(
                response_action="errors",
                errors={"access_role__workspace__SYSTEM_ADMIN": t("admin_required")},
            )
            return
        actor = body["user"]["id"]
        try:
            await repository(client).replace_role_assignments(actor, assignments)
        except ApprovalPermissionError:
            await ack(
                response_action="errors",
                errors={"access_role__workspace__SYSTEM_ADMIN": t("unauthorized")},
            )
            return
        except ConfigurationError:
            await ack(
                response_action="errors",
                errors={"access_role__workspace__SYSTEM_ADMIN": t("configuration_error")},
            )
            return
        await ack()
        await safe_dm(client, actor, t("roles_saved"))
        await publish_home(client, actor)
