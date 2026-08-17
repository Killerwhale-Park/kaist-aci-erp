import json
import logging

from pydantic import ValidationError

from app.application.profiles import UpdateApplicantProfileCommand
from app.domain.catalog import budget_nodes, categories, departments
from app.domain.enums import ApplicantType
from app.domain.models import ApplicantProfile
from app.i18n import t
from app.slack.deferred import defer
from app.slack.modals import (
    applicant_profile_modal,
    configuration_notice_modal,
    expense_context_modal,
    loading_modal,
)
from app.slack.runtime import SlackRuntime
from app.slack.utils import state_value

logger = logging.getLogger(__name__)


def _opened_view_id(response) -> str | None:
    if response is None:
        return None
    return (response.get("view") or {}).get("id")


def register_profile_handlers(slack_app, runtime: SlackRuntime) -> None:
    """Register the self-service applicant profile flow."""

    @slack_app.action("configure_applicant_profile")
    async def configure_applicant_profile(ack, body, client):
        actor = body["user"]["id"]
        await ack()

        async def load_profile() -> None:
            view_id: str | None = None
            try:
                response = await runtime.surfaces(client).open_modal(
                    body["trigger_id"], loading_modal(), actor
                )
                view_id = _opened_view_id(response)
                if view_id is None:
                    return
                profile = await runtime.repository(client).applicant_profile(actor)
                await runtime.surfaces(client).update_modal(
                    view_id,
                    applicant_profile_modal(actor, profile),
                    actor,
                )
            except Exception:
                logger.exception("Failed to load applicant profile")
                if view_id is not None:
                    await runtime.surfaces(client).update_modal(
                        view_id,
                        configuration_notice_modal(t("configuration_load_error")),
                        actor,
                    )

        await defer(load_profile)

    @slack_app.action("profile_applicant_type_changed")
    async def profile_applicant_type_changed(ack, body, client):
        actor = body["user"]["id"]
        selected_type = ApplicantType(body["actions"][0]["selected_option"]["value"])
        metadata = json.loads(body["view"].get("private_metadata") or "{}")
        saved = metadata.get("saved_profile") or {}
        matching_saved_profile = None
        if saved.get("applicant_type") == selected_type.value:
            matching_saved_profile = ApplicantProfile(
                slack_user_id=actor,
                applicant_type=selected_type,
                applicant_identifier=saved.get("applicant_identifier") or "",
            )
        continuation = {
            key: value
            for key, value in metadata.items()
            if key not in {"slack_user_id", "saved_profile"}
        }
        await ack()

        async def update_profile_form() -> None:
            await runtime.surfaces(client).update_modal(
                body["view"]["id"],
                applicant_profile_modal(
                    actor,
                    matching_saved_profile,
                    applicant_type=selected_type,
                    continuation=continuation,
                ),
                actor,
                view_hash=body["view"].get("hash"),
            )

        await defer(update_profile_form)

    @slack_app.view("applicant_profile")
    async def submit_applicant_profile(ack, body, client):
        actor = body["user"]["id"]
        state = body["view"]["state"]
        metadata = json.loads(body["view"].get("private_metadata") or "{}")
        try:
            command = UpdateApplicantProfileCommand(
                slack_user_id=actor,
                applicant_type=state_value(
                    state,
                    "profile_applicant_type",
                    "profile_applicant_type_changed",
                ),
                applicant_identifier=state_value(state, "profile_identifier"),
            )
        except ValidationError:
            await ack(
                response_action="errors",
                errors={"profile_identifier": t("validation_error")},
            )
            return
        view_id = body["view"]["id"]
        await ack(response_action="update", view=loading_modal(t("saving")))

        async def save_profile() -> None:
            try:
                profile = await runtime.repository(client).save_applicant_profile(
                    actor,
                    applicant_type=command.applicant_type,
                    applicant_identifier=command.applicant_identifier,
                )
                if metadata.get("continue_to_expense"):
                    view = expense_context_modal(
                        profile,
                        departments(),
                        budget_nodes(),
                        category_node_ids=(item.id for item in categories()),
                        initial_department_id=metadata.get("initial_department_id"),
                        source_work_request_id=metadata.get("source_work_request_id"),
                        selected_budget_node_ids=tuple(
                            metadata.get("selected_budget_node_ids") or ()
                        ),
                        selection_locked=bool(metadata.get("selection_locked")),
                    )
                else:
                    view = configuration_notice_modal(t("profile_saved"))
                await runtime.surfaces(client).update_modal(view_id, view, actor)
                await runtime.publish_homes(client, actor)
            except Exception:
                logger.exception("Failed to save applicant profile")
                await runtime.surfaces(client).update_modal(
                    view_id,
                    configuration_notice_modal(t("submission_error")),
                    actor,
                )

        await defer(save_profile)
