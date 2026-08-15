from app.application.dashboard import UserDashboard
from app.application.work_items import WorkItem, WorkItemAction
from app.domain.enums import ApplicantType
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
                    "text": "정산·구매 요청과 승인 업무를 Slack에서 처리합니다.",
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
                    "text": {"type": "plain_text", "text": "새로고침"},
                    "value": "refresh",
                }
            ],
        },
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*새 요청*"}},
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
    blocks.extend(_work_queue_section("내가 처리할 일", dashboard.work_queue.action_required))
    blocks.extend(_work_queue_section("진행 중인 요청", dashboard.work_queue.submitted))

    if dashboard.capabilities.can_manage_configuration:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*시스템 설정*\n역할, 운영 채널과 승인 절차를 관리합니다.",
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "manage_rules",
                        "text": {"type": "plain_text", "text": "설정 열기"},
                        "value": "rules",
                    },
                },
            ]
        )
    return {"type": "home", "blocks": blocks[:100]}


def _profile_block(dashboard: UserDashboard) -> dict:
    profile = dashboard.applicant_profile
    if profile is None:
        text = "*내 정보*\n정산 신청 전에 학생/교수 구분과 학번 또는 사번을 설정해주세요."
        label = "정보 설정"
        style = "primary"
    else:
        kind = "학생" if profile.applicant_type == ApplicantType.STUDENT else "교수"
        text = f"*내 정보*\n{kind} · {escape_mrkdwn(profile.applicant_identifier)}"
        label = "정보 수정"
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
        actions.append(_button("new_expense_request", "정산 신청", "new", primary=True))
    if access.can_request and access.purchase_ready:
        actions.append(_button("new_purchase_work_request", "구매 요청", "purchase"))
    if access.can_assign_settlement and access.purchase_ready:
        actions.append(_button("new_settlement_work_request", "정산 요청 보내기", "settlement"))
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
        return "신청 권한이 없습니다. 운영 관리자에게 역할을 요청하세요."
    missing: list[str] = []
    if not access.expense_ready:
        missing.append("승인 절차")
    if not access.purchase_ready:
        missing.append("운영 채널")
    if missing:
        return f"관리자 설정 필요: {', '.join(missing)}"
    return None


_ACTION_PRESENTATION = {
    WorkItemAction.VIEW_EXPENSE: ("view_request", "보기"),
    WorkItemAction.VIEW_WORK: ("view_work_request", "보기"),
    WorkItemAction.EDIT_EXPENSE: ("edit_request", "수정"),
    WorkItemAction.SUBMIT_POST_EVIDENCE: ("add_post_evidence", "사후 증빙"),
    WorkItemAction.APPROVE_EXPENSE: ("approve_request", "승인"),
    WorkItemAction.REQUEST_EXPENSE_CHANGES: ("request_changes", "수정 요청"),
    WorkItemAction.REJECT_EXPENSE: ("reject_request", "반려"),
    WorkItemAction.APPROVE_WORK: ("approve_work_request", "승인"),
    WorkItemAction.REJECT_WORK: ("reject_work_request", "반려"),
    WorkItemAction.HANDOFF_PURCHASE: ("handoff_purchase_to_settlement", "결제 완료"),
    WorkItemAction.START_SETTLEMENT: ("start_assigned_settlement", "정산 시작"),
    WorkItemAction.COMPLETE_WORK: ("complete_work_request", "완료"),
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
                "elements": [{"type": "mrkdwn", "text": "현재 항목이 없습니다."}],
            }
        )
        return blocks
    for item in items[:10]:
        title_text = escape_mrkdwn(item.title_ko or item.title_en)
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
            action_id, label = _ACTION_PRESENTATION[action]
            button = {
                "type": "button",
                "action_id": action_id,
                "text": {"type": "plain_text", "text": label},
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
    return {
        "IN_APPROVAL": "승인 진행 중",
        "CHANGES_REQUESTED": "수정 필요",
        "APPROVED_PENDING_POST_EVIDENCE": "사후 증빙 필요",
        "ACTION_REQUIRED": "처리 필요",
        "OPEN": "처리 필요",
        "REJECTED": "반려",
        "COMPLETED": "완료",
    }.get(status, escape_mrkdwn(status))
