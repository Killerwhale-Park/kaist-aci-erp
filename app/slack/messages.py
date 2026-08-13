from decimal import Decimal

from app.domain.enums import (
    ApprovalStepStatus,
    EvidenceSubmissionStatus,
    RequestStatus,
    WorkRequestKind,
    WorkRequestStatus,
)
from app.domain.models import ExpenseRequest, WorkRequest
from app.i18n import display_name, t
from app.slack.utils import escape_mrkdwn

STATUS_KEYS = {
    RequestStatus.IN_APPROVAL: "status_in_approval",
    RequestStatus.CHANGES_REQUESTED: "status_changes_requested",
    RequestStatus.REJECTED: "status_rejected",
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
    department_name = display_name(
        escape_mrkdwn(request.department.name_en), escape_mrkdwn(request.department.name_ko)
    )
    budget_name = display_name(
        escape_mrkdwn(request.budget_program.name_en),
        escape_mrkdwn(request.budget_program.name_ko),
    )
    category_name = display_name(
        escape_mrkdwn(request.category.name_en), escape_mrkdwn(request.category.name_ko)
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
        name = display_name(escape_mrkdwn(evidence.name_en), escape_mrkdwn(evidence.name_ko))
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
        f"{STEP_MARKERS[step.status]} "
        f"{display_name(escape_mrkdwn(step.name_en), escape_mrkdwn(step.name_ko))}"
        for step in request.approval_steps
    ]
    progress_text = f"*{t('approval_progress')}*\n" + "\n".join(progress_lines)
    if request.status == RequestStatus.IN_APPROVAL:
        current = next(
            step for step in request.approval_steps if step.step_order == request.current_step_order
        )
        reviewer = ", ".join(f"<@{item.slack_user_id}>" for item in current.approvers)
        if not reviewer:
            reviewer = t("unassigned")
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


def work_request_fallback_text(request: WorkRequest) -> str:
    title = (
        t("new_purchase_request")
        if request.kind == WorkRequestKind.PURCHASE
        else t("new_settlement_request")
    )
    return f"{title} {request.reference_number}"


def work_request_blocks(request: WorkRequest) -> list[dict]:
    title = (
        t("new_purchase_request")
        if request.kind == WorkRequestKind.PURCHASE
        else t("new_settlement_request")
    )
    status = (
        t("work_status_open")
        if request.status == WorkRequestStatus.OPEN
        else t("work_status_completed")
    )
    fields = [
        {"type": "mrkdwn", "text": f"*{t('requester')}*\n<@{request.requester_slack_user_id}>"},
        {"type": "mrkdwn", "text": f"*{t('assignee')}*\n<@{request.assignee_slack_user_id}>"},
        {
            "type": "mrkdwn",
            "text": f"*{t('department')}*\n{escape_mrkdwn(request.department.name_en)}",
        },
        {"type": "mrkdwn", "text": f"*{t('status')}*\n{status}"},
        {"type": "mrkdwn", "text": f"*{t('purchase_subject')}*\n{escape_mrkdwn(request.subject)}"},
    ]
    if request.quantity is not None:
        fields.append({"type": "mrkdwn", "text": f"*{t('quantity')}*\n{request.quantity:,}"})
    if request.amount is not None:
        fields.append(
            {"type": "mrkdwn", "text": f"*{t('amount')}*\n{format_amount(request.amount, 'KRW')}"}
        )
    if request.vendor:
        fields.append(
            {"type": "mrkdwn", "text": f"*{t('vendor')}*\n{escape_mrkdwn(request.vendor)}"}
        )
    if request.payment_date:
        fields.append(
            {
                "type": "mrkdwn",
                "text": f"*{t('payment_date')}*\n{request.payment_date.isoformat()}",
            }
        )
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{title} · {request.reference_number}"[:150]},
        },
        {"type": "section", "fields": fields[:10]},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{t('purpose')}*\n{escape_mrkdwn(request.purpose)}",
            },
        },
    ]
    if request.source_url:
        url = escape_mrkdwn(request.source_url)
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{t('product_url')}*\n<{url}|{t('open')}>"},
            }
        )
    if request.evidence_folder_url:
        url = escape_mrkdwn(request.evidence_folder_url)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{t('evidence_folder')}*\n<{url}|{t('open')}>",
                },
            }
        )
    if request.status == WorkRequestStatus.OPEN:
        actions = []
        if request.kind == WorkRequestKind.SETTLEMENT:
            actions.append(
                {
                    "type": "button",
                    "action_id": "start_assigned_settlement",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": t("start_settlement")},
                    "value": request.id,
                }
            )
        actions.append(
            {
                "type": "button",
                "action_id": "complete_work_request",
                "text": {"type": "plain_text", "text": t("mark_completed")},
                "value": request.id,
            }
        )
        blocks.append({"type": "actions", "elements": actions})
    return blocks
