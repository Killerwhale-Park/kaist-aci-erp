from dataclasses import dataclass

from app.application.work_items import UserWorkQueue, WorkItem, WorkItemAction
from app.domain.enums import UserRole
from app.domain.models import UserProfile
from app.i18n import display_name, t
from app.slack.utils import escape_mrkdwn


@dataclass(frozen=True)
class HomeCapabilities:
    can_request: bool = False
    expense_ready: bool = False
    purchase_ready: bool = False
    can_assign_settlement: bool = False


def app_home_view(
    profile: UserProfile,
    work_queue: UserWorkQueue,
    capabilities: HomeCapabilities | None = None,
) -> dict:
    access = capabilities or HomeCapabilities()
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": t("app_title")}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("home_intro")},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{t('start_request')}*"},
        },
    ]

    if access.can_request and access.expense_ready:
        blocks.append(
            _start_action(
                "new_expense_request",
                t("new_request"),
                t("expense_start_help"),
                "new",
                primary=True,
            )
        )
    if access.can_request and access.purchase_ready:
        blocks.append(
            _start_action(
                "new_purchase_work_request",
                t("new_purchase_request"),
                t("purchase_start_help"),
                "purchase",
            )
        )
    if access.can_assign_settlement and access.purchase_ready:
        blocks.append(
            _start_action(
                "new_settlement_work_request",
                t("new_settlement_request"),
                t("settlement_start_help"),
                "settlement",
            )
        )

    if not access.can_request:
        blocks.append(_notice(t("request_role_required")))
    elif not access.expense_ready and not access.purchase_ready:
        blocks.append(_notice(t("request_configuration_missing")))
    elif not access.expense_ready:
        blocks.append(_notice(t("expense_configuration_missing")))
    elif not access.purchase_ready:
        blocks.append(_notice(t("operating_channel_missing")))

    blocks.extend(_work_queue_section(t("my_active_requests"), work_queue.submitted, "no_requests"))
    blocks.extend(
        _work_queue_section(
            t("my_action_required"),
            work_queue.action_required,
            "no_action_required",
        )
    )

    if profile.role == UserRole.SYSTEM_ADMIN:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{t('system_administration')}*"},
                    "accessory": {
                        "type": "button",
                        "action_id": "manage_rules",
                        "text": {"type": "plain_text", "text": t("manage_rules")},
                        "value": "rules",
                    },
                },
            ]
        )
    return {"type": "home", "blocks": blocks[:100]}


def _start_action(
    action_id: str,
    label: str,
    help_text: str,
    value: str,
    *,
    primary: bool = False,
) -> dict:
    button = {
        "type": "button",
        "action_id": action_id,
        "text": {"type": "plain_text", "text": label},
        "value": value,
    }
    if primary:
        button["style"] = "primary"
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": help_text},
        "accessory": button,
    }


def _notice(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


_ACTION_PRESENTATION = {
    WorkItemAction.VIEW_EXPENSE: ("view_request", "view"),
    WorkItemAction.VIEW_WORK: ("view_work_request", "view"),
    WorkItemAction.EDIT_EXPENSE: ("edit_request", "edit_request"),
    WorkItemAction.SUBMIT_POST_EVIDENCE: ("add_post_evidence", "submit_post_evidence"),
    WorkItemAction.APPROVE_EXPENSE: ("approve_request", "approve"),
    WorkItemAction.REQUEST_EXPENSE_CHANGES: ("request_changes", "request_changes"),
    WorkItemAction.REJECT_EXPENSE: ("reject_request", "reject"),
    WorkItemAction.APPROVE_WORK: ("approve_work_request", "approve"),
    WorkItemAction.REJECT_WORK: ("reject_work_request", "reject"),
    WorkItemAction.HANDOFF_PURCHASE: (
        "handoff_purchase_to_settlement",
        "payment_complete_handoff",
    ),
    WorkItemAction.START_SETTLEMENT: ("start_assigned_settlement", "start_settlement"),
    WorkItemAction.COMPLETE_WORK: ("complete_work_request", "mark_completed"),
}


def _work_queue_section(title: str, items: tuple[WorkItem, ...], empty_key: str) -> list[dict]:
    blocks: list[dict] = [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}},
    ]
    if not items:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": t(empty_key)}})
        return blocks
    for item in items[:10]:
        title_text = display_name(escape_mrkdwn(item.title_en), escape_mrkdwn(item.title_ko))
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{escape_mrkdwn(item.reference_number)}*\n"
                        f"{title_text}\n{_work_item_status(item.status)}"
                    ),
                },
            }
        )
        actions = []
        for action in item.actions:
            action_id, label_key = _ACTION_PRESENTATION[action]
            button = {
                "type": "button",
                "action_id": action_id,
                "text": {"type": "plain_text", "text": t(label_key)},
                "value": item.source_id,
            }
            if action in {WorkItemAction.APPROVE_EXPENSE, WorkItemAction.APPROVE_WORK}:
                button["style"] = "primary"
            elif action in {WorkItemAction.REJECT_EXPENSE, WorkItemAction.REJECT_WORK}:
                button["style"] = "danger"
            actions.append(button)
        if actions:
            blocks.append({"type": "actions", "elements": actions})
    return blocks


def _work_item_status(status: str) -> str:
    key = {
        "IN_APPROVAL": "status_in_approval",
        "CHANGES_REQUESTED": "status_changes_requested",
        "APPROVED_PENDING_POST_EVIDENCE": "status_approved_pending_post_evidence",
        "ACTION_REQUIRED": "work_status_action_required",
        "OPEN": "work_status_action_required",
        "REJECTED": "status_rejected",
        "COMPLETED": "status_completed",
    }.get(status)
    return t(key) if key else escape_mrkdwn(status)
