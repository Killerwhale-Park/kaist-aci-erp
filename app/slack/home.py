from app.domain.enums import EvidenceSubmissionStatus, EvidenceTiming, RequestStatus, UserRole
from app.domain.models import BudgetProgram, ExpenseRequest, UserProfile
from app.i18n import display_name, t
from app.slack.messages import status_text
from app.slack.utils import escape_mrkdwn


def app_home_view(
    profile: UserProfile,
    budgets: list[BudgetProgram],
    own_requests: list[ExpenseRequest],
    pending_approvals: list[ExpenseRequest],
    can_assign_settlement: bool = False,
    can_submit_requests: bool = False,
) -> dict:
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": t("app_title")}}
    ]
    if can_submit_requests:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "new_expense_request",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": f"+ {t('new_request')}"},
                        "value": "new",
                    }
                ],
            }
        )
    if can_submit_requests or can_assign_settlement:
        work_request_actions = []
        if can_submit_requests:
            work_request_actions.append(
                {
                    "type": "button",
                    "action_id": "new_purchase_work_request",
                    "text": {"type": "plain_text", "text": t("new_purchase_request")},
                    "value": "purchase",
                }
            )
        if can_assign_settlement:
            work_request_actions.append(
                {
                    "type": "button",
                    "action_id": "new_settlement_work_request",
                    "text": {"type": "plain_text", "text": t("new_settlement_request")},
                    "value": "settlement",
                }
            )
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{t('work_requests')}*"},
                },
                {"type": "actions", "elements": work_request_actions},
            ]
        )
    if not can_submit_requests:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("requester_role_required")},
            }
        )
    if can_submit_requests:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{t('available_programs')}*"},
                },
            ]
        )
    for budget in budgets if can_submit_requests else []:
        text = f"*{display_name(escape_mrkdwn(budget.name_en), escape_mrkdwn(budget.name_ko))}*"
        if budget.is_available:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                    "accessory": {
                        "type": "button",
                        "action_id": "new_expense_request",
                        "text": {"type": "plain_text", "text": t("open")},
                        "value": budget.id,
                    },
                }
            )
        else:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{text}\n_{t('coming_soon')}_"},
                }
            )

    blocks.extend(
        [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{t('my_requests')}*"}},
        ]
    )
    if not own_requests:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": t("no_requests")}})
    for request in own_requests:
        category_name = display_name(
            escape_mrkdwn(request.category.name_en), escape_mrkdwn(request.category.name_ko)
        )
        actions = [
            {
                "type": "button",
                "action_id": "view_request",
                "text": {"type": "plain_text", "text": t("view")},
                "value": str(request.id),
            }
        ]
        if request.status == RequestStatus.CHANGES_REQUESTED:
            actions.append(
                {
                    "type": "button",
                    "action_id": "edit_request",
                    "text": {"type": "plain_text", "text": t("edit_request")},
                    "value": str(request.id),
                }
            )
        has_missing_post_evidence = any(
            evidence.timing == EvidenceTiming.POST
            and evidence.status == EvidenceSubmissionStatus.MISSING
            for evidence in request.evidence_submissions
        )
        if (
            request.status
            in {
                RequestStatus.APPROVED_PENDING_POST_EVIDENCE,
                RequestStatus.COMPLETED,
            }
            and has_missing_post_evidence
        ):
            actions.append(
                {
                    "type": "button",
                    "action_id": "add_post_evidence",
                    "text": {"type": "plain_text", "text": t("submit_post_evidence")},
                    "value": str(request.id),
                }
            )
        blocks.extend(
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{request.reference_number}*\n"
                            f"{category_name}\n"
                            f"{status_text(request.status)}"
                        ),
                    },
                },
                {"type": "actions", "elements": actions},
            ]
        )

    if pending_approvals:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{t('pending_approvals')}*"},
                },
            ]
        )
        for request in pending_approvals[:10]:
            pending_text = (
                f"*{request.reference_number}* — {escape_mrkdwn(request.category.name_en)}"
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": pending_text,
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "view_request",
                        "text": {"type": "plain_text", "text": t("view")},
                        "value": str(request.id),
                    },
                }
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
