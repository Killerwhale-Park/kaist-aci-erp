import json
import logging
import uuid
from typing import Any

from pydantic import ValidationError
from slack_sdk.errors import SlackApiError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.approvals.resolver import ApprovalRuleResolver
from app.approvals.service import ApprovalService
from app.config.settings import Settings
from app.db.enums import ApplicantType, ApproverType, RequestStatus, UserRole
from app.db.models import BudgetProgram, Department, ExpenseCategory, UserProfile
from app.db.repository import ExpenseRequestRepository
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
from app.expenses.service import ExpenseService
from app.i18n import t
from app.slack.home import app_home_view
from app.slack.messages import request_fallback_text, request_message_blocks
from app.slack.modals import (
    administration_modal,
    approval_decision_modal,
    edit_expense_modal,
    expense_context_modal,
    expense_details_modal,
    post_evidence_modal,
    request_details_modal,
)
from app.slack.utils import state_value
from app.users.service import UserProfileService

logger = logging.getLogger(__name__)


def register_handlers(
    slack_app, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    def session_context():
        from contextlib import contextmanager

        @contextmanager
        def manager():
            session = session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return manager()

    async def open_new_request(client, trigger_id: str, slack_user_id: str) -> None:
        with session_context() as session:
            departments = list(
                session.scalars(
                    select(Department).where(Department.is_active.is_(True)).order_by(Department.id)
                )
            )
            budgets = list(
                session.scalars(
                    select(BudgetProgram)
                    .where(BudgetProgram.is_active.is_(True), BudgetProgram.is_available.is_(True))
                    .order_by(BudgetProgram.id)
                )
            )
            categories = list(
                session.scalars(
                    select(ExpenseCategory)
                    .where(ExpenseCategory.is_active.is_(True))
                    .order_by(ExpenseCategory.id)
                )
            )
            view = expense_context_modal(slack_user_id, departments, budgets, categories)
        await client.views_open(trigger_id=trigger_id, view=view)

    async def publish_home(client, slack_user_id: str) -> None:
        with session_context() as session:
            profiles = UserProfileService(session, settings)
            profile = profiles.get_or_create(slack_user_id)
            session.flush()
            repository = ExpenseRequestRepository(session)
            own_requests = repository.list_for_applicant(slack_user_id)
            approval_service = ApprovalService(session)
            pending = [
                request
                for request in repository.list_in_approval()
                if approval_service.can_actor_approve(request, slack_user_id)
            ]
            budgets = list(
                session.scalars(
                    select(BudgetProgram)
                    .where(BudgetProgram.is_active.is_(True))
                    .order_by(BudgetProgram.is_available.desc(), BudgetProgram.id)
                )
            )
            view = app_home_view(profile, budgets, own_requests, pending)
        await client.views_publish(user_id=slack_user_id, view=view)

    async def synchronize_approval_message(client, request_id: str | uuid.UUID) -> None:
        with session_context() as session:
            request = ExpenseRequestRepository(session).get(request_id)
            channel = request.approval_channel_id
            message_ts = request.approval_message_ts
            blocks = request_message_blocks(request)
            text = request_fallback_text(request)
        try:
            if message_ts:
                await client.chat_update(channel=channel, ts=message_ts, text=text, blocks=blocks)
                return
            response = await client.chat_postMessage(channel=channel, text=text, blocks=blocks)
            with session_context() as session:
                stored = ExpenseRequestRepository(session).get(request_id, for_update=True)
                stored.approval_message_ts = response["ts"]
        except SlackApiError:
            logger.exception("Failed to synchronize approval message for request %s", request_id)

    async def safe_dm(
        client, slack_user_id: str, text: str, blocks: list[dict] | None = None
    ) -> None:
        try:
            await client.chat_postMessage(channel=slack_user_id, text=text, blocks=blocks)
        except SlackApiError:
            logger.exception("Failed to send Slack DM to %s", slack_user_id)

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
        client, request_id: str | uuid.UUID, reason: str | None = None
    ) -> None:
        with session_context() as session:
            request = ExpenseRequestRepository(session).get(request_id)
            applicant = request.applicant_slack_user_id
            reference = request.reference_number
            status = request.status
            current_step = None
            if status == RequestStatus.IN_APPROVAL:
                current_step = next(
                    step
                    for step in request.approval_steps
                    if step.step_order == request.current_step_order
                )
        if status == RequestStatus.CHANGES_REQUESTED:
            text = t("changes_requested_notice", reference=reference, reason=reason or "-")
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "edit_request",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": t("edit_request")},
                            "value": str(request_id),
                        }
                    ],
                },
            ]
            await safe_dm(client, applicant, text, blocks)
            return
        if status == RequestStatus.REJECTED:
            await safe_dm(
                client,
                applicant,
                t("rejected_notice", reference=reference, reason=reason or "-"),
            )
            return
        if status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE:
            text = t("post_evidence_needed", reference=reference)
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "add_post_evidence",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": t("submit_post_evidence")},
                            "value": str(request_id),
                        }
                    ],
                },
            ]
            await safe_dm(client, applicant, text, blocks)
            return
        if status == RequestStatus.COMPLETED:
            await safe_dm(client, applicant, t("completed_notice", reference=reference))
            return
        await safe_dm(client, applicant, t("request_updated", reference=reference))
        if current_step and current_step.approver_type == ApproverType.SLACK_USER:
            await safe_dm(
                client,
                current_step.approver_reference,
                t("next_review_notice", reference=reference),
            )

    async def refresh_display_name(client, request_id: str | uuid.UUID, slack_user_id: str) -> None:
        try:
            response = await client.users_info(user=slack_user_id)
            slack_profile = response["user"].get("profile", {})
            display_name = (
                slack_profile.get("display_name")
                or slack_profile.get("real_name")
                or response["user"].get("name")
                or slack_user_id
            )
            with session_context() as session:
                UserProfileService(session, settings).get_or_create(slack_user_id, display_name)
                request = ExpenseRequestRepository(session).get(request_id, for_update=True)
                request.applicant_display_name = display_name
        except SlackApiError:
            logger.exception("Failed to refresh Slack display name for %s", slack_user_id)

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
            if field == "amount":
                errors[block_id] = t("amount_invalid")
            else:
                errors[block_id] = t("validation_error")
        return errors or {"purpose": t("validation_error")}

    def domain_errors(error: DomainValidationError) -> dict[str, str]:
        return error.field_errors or {"purpose": t("validation_error")}

    async def open_owned_request_modal(
        client, trigger_id: str, request_id: str, actor: str, mode: str
    ):
        with session_context() as session:
            request = ExpenseRequestRepository(session).get(request_id)
            profile = session.get(UserProfile, actor)
            approval_service = ApprovalService(session)
            can_view = (
                request.applicant_slack_user_id == actor
                or bool(profile and profile.role == UserRole.SYSTEM_ADMIN)
                or approval_service.can_actor_approve(request, actor)
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
                has_missing_post_evidence = any(
                    evidence.timing.value == "POST" and not evidence.url
                    for evidence in request.evidence_submissions
                )
                if (
                    request.status
                    not in {
                        RequestStatus.APPROVED_PENDING_POST_EVIDENCE,
                        RequestStatus.COMPLETED,
                    }
                    or not has_missing_post_evidence
                ):
                    raise InvalidStateTransitionError
                view = post_evidence_modal(request)
            else:
                view = request_details_modal(request)
        await client.views_open(trigger_id=trigger_id, view=view)

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
        await open_new_request(client, body["trigger_id"], body["user"]["id"])

    @slack_app.view("expense_context")
    async def submit_expense_context(ack, body):
        state = body["view"]["state"]
        applicant_type = state_value(state, "applicant_type")
        student_id = state_value(state, "student_id")
        if applicant_type == ApplicantType.STUDENT.value and not student_id:
            await ack(response_action="errors", errors={"student_id": t("student_id_required")})
            return
        context = {
            "department_id": state_value(state, "department"),
            "applicant_type": applicant_type,
            "student_id": student_id,
            "budget_program_id": state_value(state, "budget"),
            "category_id": state_value(state, "category"),
        }
        with session_context() as session:
            category = session.get(ExpenseCategory, context["category_id"])
            budget = session.get(BudgetProgram, context["budget_program_id"])
            if category is None or budget is None or category.budget_program_id != budget.id:
                await ack(response_action="errors", errors={"category": t("configuration_error")})
                return
            requirements = ApprovalRuleResolver(session).evidence_requirements(category.id)
            view = expense_details_modal(context, requirements)
        await ack(response_action="update", view=view)

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
                student_id=context.get("student_id"),
                budget_program_id=context["budget_program_id"],
                category_id=context["category_id"],
                amount=state_value(state, "amount"),
                vendor=state_value(state, "vendor"),
                payment_date=state_value(state, "payment_date"),
                purpose=state_value(state, "purpose"),
                evidence_folder_url=state_value(state, "evidence_folder"),
                evidence=evidence_from_state(state),
            )
            with session_context() as session:
                request = ExpenseService(
                    session, UserProfileService(session, settings)
                ).create_and_submit(command)
                request_id = request.id
                reference = request.reference_number
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
        except Exception:
            logger.exception("Failed to create an expense request")
            await ack(response_action="errors", errors={"purpose": t("submission_error")})
            return

        await refresh_display_name(client, request_id, actor)
        await synchronize_approval_message(client, request_id)
        warning_links = drive_warning_urls(
            [command.evidence_folder_url] + [item.url for item in command.evidence.values()]
        )
        confirmation = t("request_submitted", reference=reference)
        if warning_links:
            confirmation += f"\n\n{t('non_drive_warning')}"
        await safe_dm(client, actor, confirmation)
        await notify_after_transition(client, request_id)
        await publish_home(client, actor)

    @slack_app.action("approve_request")
    async def approve_request(ack, body, client, respond):
        await ack()
        request_id = body["actions"][0]["value"]
        actor = body["user"]["id"]
        try:
            with session_context() as session:
                ApprovalService(session).approve(request_id, actor)
        except ApprovalPermissionError:
            await send_ephemeral(client, body, t("unauthorized"), respond)
            return
        except InvalidStateTransitionError:
            await send_ephemeral(client, body, t("invalid_state"), respond)
            return
        await synchronize_approval_message(client, request_id)
        await notify_after_transition(client, request_id)
        await publish_home(client, actor)

    async def open_decision(ack, body, client, respond, decision: str) -> None:
        await ack()
        request_id = body["actions"][0]["value"]
        actor = body["user"]["id"]
        try:
            with session_context() as session:
                ApprovalService(session).assert_actor_can_approve(request_id, actor)
        except (ApprovalPermissionError, InvalidStateTransitionError):
            await send_ephemeral(client, body, t("unauthorized"), respond)
            return
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=approval_decision_modal(request_id, decision),
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
        try:
            with session_context() as session:
                service = ApprovalService(session)
                if metadata["decision"] == "changes":
                    service.request_changes(metadata["request_id"], actor, reason)
                else:
                    service.reject(metadata["request_id"], actor, reason)
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
        await synchronize_approval_message(client, metadata["request_id"])
        await notify_after_transition(client, metadata["request_id"], reason)
        await publish_home(client, actor)

    async def open_request_action(ack, body, client, mode: str):
        await ack()
        actor = body["user"]["id"]
        request_id = body["actions"][0]["value"]
        try:
            await open_owned_request_modal(client, body["trigger_id"], request_id, actor, mode)
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
            with session_context() as session:
                ExpenseService(session, UserProfileService(session, settings)).resubmit(
                    metadata["request_id"], actor, command
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
        except Exception:
            logger.exception("Failed to resubmit an expense request")
            await ack(response_action="errors", errors={"purpose": t("submission_error")})
            return
        await synchronize_approval_message(client, metadata["request_id"])
        await notify_after_transition(client, metadata["request_id"])
        warning_links = drive_warning_urls(
            [command.evidence_folder_url] + [item.url for item in command.evidence.values()]
        )
        if warning_links:
            await safe_dm(client, actor, t("non_drive_warning"))
        await publish_home(client, actor)

    @slack_app.view("post_evidence")
    async def submit_post_evidence(ack, body, client):
        metadata = json.loads(body["view"]["private_metadata"])
        actor = body["user"]["id"]
        try:
            command = PostEvidenceCommand(evidence=evidence_from_state(body["view"]["state"]))
            with session_context() as session:
                ExpenseService(session, UserProfileService(session, settings)).submit_post_evidence(
                    metadata["request_id"], actor, command
                )
            await ack()
        except ValidationError as error:
            await ack(response_action="errors", errors=pydantic_errors(error))
            return
        except DomainValidationError as error:
            fallback_block = next(
                (
                    block_id
                    for block_id in body["view"]["state"].get("values", {})
                    if block_id.startswith("evidence__")
                ),
                "evidence__post_evidence",
            )
            await ack(
                response_action="errors",
                errors=error.field_errors or {fallback_block: t("validation_error")},
            )
            return
        except ApprovalPermissionError:
            fallback_block = next(
                block_id
                for block_id in body["view"]["state"]["values"]
                if block_id.startswith("evidence__")
            )
            await ack(response_action="errors", errors={fallback_block: t("not_applicant")})
            return
        except InvalidStateTransitionError:
            fallback_block = next(
                block_id
                for block_id in body["view"]["state"]["values"]
                if block_id.startswith("evidence__")
            )
            await ack(response_action="errors", errors={fallback_block: t("invalid_state")})
            return
        except Exception:
            logger.exception("Failed to submit post-event evidence")
            fallback_block = next(
                block_id
                for block_id in body["view"]["state"]["values"]
                if block_id.startswith("evidence__")
            )
            await ack(response_action="errors", errors={fallback_block: t("submission_error")})
            return
        await synchronize_approval_message(client, metadata["request_id"])
        await notify_after_transition(client, metadata["request_id"])
        warning_links = drive_warning_urls([item.url for item in command.evidence.values()])
        if warning_links:
            await safe_dm(client, actor, t("non_drive_warning"))
        await publish_home(client, actor)

    @slack_app.action("manage_rules")
    async def manage_rules_action(ack, body, client):
        await ack()
        actor = body["user"]["id"]
        with session_context() as session:
            profile = session.get(UserProfile, actor)
            if profile is None or profile.role != UserRole.SYSTEM_ADMIN:
                await safe_dm(client, actor, t("unauthorized"))
                return
        await client.views_open(trigger_id=body["trigger_id"], view=administration_modal())
