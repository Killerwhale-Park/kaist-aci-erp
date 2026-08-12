from decimal import Decimal

from app.db.enums import ApprovalStepStatus, ApproverType, EvidenceSubmissionStatus, RequestStatus
from app.db.models import ExpenseRequest
from app.i18n import t
from app.slack.utils import escape_mrkdwn

STATUS_KEYS = {
    RequestStatus.DRAFT: "status_draft",
    RequestStatus.SUBMITTED: "status_submitted",
    RequestStatus.IN_APPROVAL: "status_in_approval",
    RequestStatus.CHANGES_REQUESTED: "status_changes_requested",
    RequestStatus.REJECTED: "status_rejected",
    RequestStatus.APPROVED: "status_approved",
    RequestStatus.APPROVED_PENDING_POST_EVIDENCE: "status_approved_pending_post_evidence",
    RequestStatus.COMPLETED: "status_completed",
}

STEP_MARKERS = {
    ApprovalStepStatus.WAITING: "○",
    ApprovalStepStatus.PENDING: "●",
    ApprovalStepStatus.APPROVED: "✓",
    ApprovalStepStatus.CHANGES_REQUESTED: "↺",
    ApprovalStepStatus.REJECTED: "✕",
}


def status_text(status: RequestStatus) -> str:
    return t(STATUS_KEYS[status])


def format_amount(amount: Decimal, currency: str) -> str:
    if currency == "KRW":
        return f"₩{amount:,.0f}"
    return f"{currency} {amount:,.2f}"


def request_message_blocks(request: ExpenseRequest, *, include_actions: bool = True) -> list[dict]:
    student_id = escape_mrkdwn(request.student_id or "-")
    department_name = (
        f"{escape_mrkdwn(request.department.name_en)} / {escape_mrkdwn(request.department.name_ko)}"
    )
    budget_name = (
        f"{escape_mrkdwn(request.budget_program.name_en)} / "
        f"{escape_mrkdwn(request.budget_program.name_ko)}"
    )
    category_name = (
        f"{escape_mrkdwn(request.category.name_en)} / {escape_mrkdwn(request.category.name_ko)}"
    )
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": t("request_title", reference=request.reference_number),
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*{t('applicant')}*\n<@{request.applicant_slack_user_id}>",
                },
                {"type": "mrkdwn", "text": f"*{t('student_id')}*\n{student_id}"},
                {
                    "type": "mrkdwn",
                    "text": f"*{t('department')}*\n{department_name}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*{t('budget')}*\n{budget_name}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*{t('category')}*\n{category_name}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*{t('amount')}*\n{format_amount(request.amount, request.currency)}",
                },
                {"type": "mrkdwn", "text": f"*{t('vendor')}*\n{escape_mrkdwn(request.vendor)}"},
                {
                    "type": "mrkdwn",
                    "text": f"*{t('payment_date')}*\n{request.payment_date.isoformat()}",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{t('purpose')}*\n{escape_mrkdwn(request.purpose)}",
            },
        },
    ]
    if request.evidence_folder_url:
        folder_url = escape_mrkdwn(request.evidence_folder_url)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{t('evidence_folder')}*\n<{folder_url}|{t('open')}>",
                },
            }
        )

    evidence_lines = []
    for evidence in request.evidence_submissions:
        requirement_label = (
            t("optional") if evidence.requirement.value == "OPTIONAL" else t("required")
        )
        name = f"{escape_mrkdwn(evidence.name_en)} / {escape_mrkdwn(evidence.name_ko)}"
        if evidence.status == EvidenceSubmissionStatus.SUBMITTED and evidence.url:
            evidence_url = escape_mrkdwn(evidence.url)
            evidence_lines.append(
                f"✓ *{name}* ({evidence.timing.value}) — <{evidence_url}|{t('view')}>"
            )
        else:
            evidence_lines.append(f"○ *{name}* ({evidence.timing.value}) — {requirement_label}")
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{t('evidence')}*\n" + ("\n".join(evidence_lines) or "-"),
            },
        }
    )

    progress_lines = [
        f"{STEP_MARKERS[step.status]} {escape_mrkdwn(step.name_en)} / {escape_mrkdwn(step.name_ko)}"
        for step in request.approval_steps
    ]
    progress_text = f"*{t('approval_progress')}*\n" + "\n".join(progress_lines)
    if request.status == RequestStatus.IN_APPROVAL:
        current = next(
            step for step in request.approval_steps if step.step_order == request.current_step_order
        )
        reviewer = (
            f"<@{current.approver_reference}>"
            if current.approver_type == ApproverType.SLACK_USER
            else escape_mrkdwn(current.approver_reference)
        )
        progress_text += f"\n\n*{t('current_reviewer')}*\n{reviewer}"
    progress_text += f"\n\n*{t('status')}*\n{status_text(request.status)}"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": progress_text}})

    if include_actions and request.status == RequestStatus.IN_APPROVAL:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve_request",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": t("approve")},
                        "value": str(request.id),
                    },
                    {
                        "type": "button",
                        "action_id": "request_changes",
                        "text": {"type": "plain_text", "text": t("request_changes")},
                        "value": str(request.id),
                    },
                    {
                        "type": "button",
                        "action_id": "reject_request",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": t("reject")},
                        "value": str(request.id),
                    },
                ],
            }
        )
    return blocks


def request_fallback_text(request: ExpenseRequest) -> str:
    return (
        f"{t('request_title', reference=request.reference_number)} — {status_text(request.status)}"
    )
