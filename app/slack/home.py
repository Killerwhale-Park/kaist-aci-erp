from app.application.dashboard import UserDashboard
from app.application.work_items import WorkItem, WorkItemAction
from app.domain.enums import ApplicantType
from app.i18n import display_name, t
from app.slack.utils import escape_mrkdwn


def app_home_view(dashboard: UserDashboard) -> dict:
    """Render a compact task-oriented Home surface from an application projection."""

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "AI College ERP"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": t("home_tagline"),
                }
            ],
        },
        _profile_block(dashboard),
        {
            "type": "actions",
            "block_id": "home_controls",
            "elements": [
                {
                    "type": "button",
                    "action_id": "refresh_home",
                    "text": {"type": "plain_text", "text": t("refresh")},
                    "value": "refresh",
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{t('home_new_requests')}*"},
        },
    ]
    quick_actions = _quick_actions(dashboard)
    if quick_actions:
        blocks.append(
            {
                "type": "actions",
                "block_id": "new_request_actions",
                "elements": quick_actions,
            }
        )
    notice = _configuration_notice(dashboard)
    if notice:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": notice}],
            }
        )

    # Outstanding work is intentionally shown before sent requests.
    blocks.extend(
        _work_queue_section(t("my_action_required"), dashboard.work_queue.action_required)
    )
    blocks.extend(_work_queue_section(t("my_active_requests"), dashboard.work_queue.submitted))

    if dashboard.capabilities.can_manage_configuration:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{t('home_system_settings')}*\n{t('home_system_settings_help')}"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "manage_rules",
                        "text": {"type": "plain_text", "text": t("home_open_settings")},
                        "value": "rules",
                    },
                },
            ]
        )
    return {"type": "home", "blocks": blocks[:100]}


def _profile_block(dashboard: UserDashboard) -> dict:
    profile = dashboard.applicant_profile
    if profile is None:
        text = f"*{t('profile_heading')}*\n{t('profile_missing')}"
        label = t("profile_setup")
        style = "primary"
    else:
        kind = t("student" if profile.applicant_type == ApplicantType.STUDENT else "professor")
        text = f"*{t('profile_heading')}*\n{kind} · {escape_mrkdwn(profile.applicant_identifier)}"
        label = t("profile_edit")
        style = None
    button = {
        "type": "button",
        "action_id": "configure_applicant_profile",
        "text": {"type": "plain_text", "text": label},
        "value": "profile",
    }
    if style:
        button["style"] = style
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
        "accessory": button,
    }


def _quick_actions(dashboard: UserDashboard) -> list[dict]:
    access = dashboard.capabilities
    actions: list[dict] = []
    if access.can_request:
        actions.append(_button("new_expense_request", t("new_request_short"), "new", primary=True))
    if access.can_request and access.purchase_ready:
        actions.append(_button("new_purchase_work_request", t("new_purchase_request"), "purchase"))
    if access.can_assign_settlement and access.purchase_ready:
        actions.append(
            _button("new_settlement_work_request", t("new_settlement_request"), "settlement")
        )
    return actions


def _button(action_id: str, label: str, value: str, *, primary: bool = False) -> dict:
    button = {
        "type": "button",
        "action_id": action_id,
        "text": {"type": "plain_text", "text": label},
        "value": value,
    }
    if primary:
        button["style"] = "primary"
    return button


def _configuration_notice(dashboard: UserDashboard) -> str | None:
    access = dashboard.capabilities
    if not access.can_request:
        return t("home_request_permission_missing")
    missing: list[str] = []
    if not access.expense_ready:
        missing.append(t("configure_rules"))
    if not access.purchase_ready:
        missing.append(t("manage_system_channels"))
    if missing:
        return t("home_configuration_required", items=", ".join(missing))
    return None


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


def _work_queue_section(title: str, items: tuple[WorkItem, ...]) -> list[dict]:
    blocks: list[dict] = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*  `{len(items)}`"},
        },
    ]
    if not items:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": t("no_current_items")}],
            }
        )
        return blocks
    for item in items[:10]:
        title_text = display_name(
            escape_mrkdwn(item.title_en),
            escape_mrkdwn(item.title_ko),
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{escape_mrkdwn(item.reference_number)}* · "
                        f"{_work_item_status(item.status)}\n"
                        f"{title_text}"
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
        "OPEN": "work_status_open",
        "REJECTED": "status_rejected",
        "COMPLETED": "status_completed",
    }.get(status)
    return t(key) if key else escape_mrkdwn(status)
