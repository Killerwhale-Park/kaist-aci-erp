import json
from collections.abc import Iterable

from app.db.enums import EvidenceRequirementLevel, EvidenceTiming
from app.db.models import (
    BudgetProgram,
    Department,
    EvidenceRequirementDefinition,
    EvidenceSubmission,
    ExpenseCategory,
    ExpenseRequest,
)
from app.i18n import t
from app.slack.messages import request_message_blocks
from app.slack.utils import input_element


def _option(value: str, name_en: str, name_ko: str) -> dict:
    return {
        "text": {"type": "plain_text", "text": f"{name_en} / {name_ko}"[:75]},
        "value": value,
    }


def expense_context_modal(
    slack_user_id: str,
    departments: Iterable[Department],
    budgets: Iterable[BudgetProgram],
    categories: Iterable[ExpenseCategory],
) -> dict:
    return {
        "type": "modal",
        "callback_id": "expense_context",
        "title": {"type": "plain_text", "text": t("new_request_short")},
        "submit": {"type": "plain_text", "text": t("continue")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{t('applicant')}*\n<@{slack_user_id}>\n_{t('automatic_identity')}_",
                },
            },
            {
                "type": "input",
                "block_id": "department",
                "label": {"type": "plain_text", "text": t("department")},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [
                        _option(item.id, item.name_en, item.name_ko) for item in departments
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "applicant_type",
                "label": {"type": "plain_text", "text": t("applicant_type")},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [
                        {"text": {"type": "plain_text", "text": t("student")}, "value": "STUDENT"},
                        {"text": {"type": "plain_text", "text": t("other")}, "value": "OTHER"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "student_id",
                "optional": True,
                "label": {"type": "plain_text", "text": t("student_id")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "budget",
                "label": {"type": "plain_text", "text": t("budget")},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [_option(item.id, item.name_en, item.name_ko) for item in budgets],
                },
            },
            {
                "type": "input",
                "block_id": "category",
                "label": {"type": "plain_text", "text": t("expense_category")},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [
                        _option(item.id, item.name_en, item.name_ko) for item in categories
                    ],
                },
            },
        ],
    }


def expense_details_modal(context: dict, requirements: list[EvidenceRequirementDefinition]) -> dict:
    return {
        "type": "modal",
        "callback_id": "expense_details",
        "private_metadata": json.dumps(context),
        "title": {"type": "plain_text", "text": t("details_title")},
        "submit": {"type": "plain_text", "text": t("submit")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": _expense_fields() + _evidence_fields(requirements, EvidenceTiming.PRE),
    }


def edit_expense_modal(request: ExpenseRequest) -> dict:
    return {
        "type": "modal",
        "callback_id": "expense_edit",
        "private_metadata": json.dumps({"request_id": str(request.id)}),
        "title": {"type": "plain_text", "text": t("details_title")},
        "submit": {"type": "plain_text", "text": t("resubmit")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": _expense_fields(request)
        + _evidence_fields(request.evidence_submissions, EvidenceTiming.PRE),
    }


def post_evidence_modal(request: ExpenseRequest) -> dict:
    return {
        "type": "modal",
        "callback_id": "post_evidence",
        "private_metadata": json.dumps({"request_id": str(request.id)}),
        "title": {"type": "plain_text", "text": t("post_title")},
        "submit": {"type": "plain_text", "text": t("submit")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": _evidence_fields(request.evidence_submissions, EvidenceTiming.POST),
    }


def approval_decision_modal(request_id: str, decision: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "approval_decision",
        "private_metadata": json.dumps({"request_id": request_id, "decision": decision}),
        "title": {"type": "plain_text", "text": t("decision_title")},
        "submit": {
            "type": "plain_text",
            "text": t("request_changes") if decision == "changes" else t("reject"),
        },
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "input",
                "block_id": "decision_reason",
                "label": {"type": "plain_text", "text": t("reason")},
                "element": input_element("value", multiline=True),
            }
        ],
    }


def request_details_modal(request: ExpenseRequest) -> dict:
    return {
        "type": "modal",
        "callback_id": "request_details",
        "title": {"type": "plain_text", "text": t("request_details")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": request_message_blocks(request, include_actions=False),
    }


def administration_modal() -> dict:
    return {
        "type": "modal",
        "callback_id": "administration_info",
        "title": {"type": "plain_text", "text": t("administration_title")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("configuration_seed_notice")},
            }
        ],
    }


def _expense_fields(request: ExpenseRequest | None = None) -> list[dict]:
    initial_amount = None
    if request is not None:
        initial_amount = f"{request.amount:.2f}".rstrip("0").rstrip(".")
    blocks = [
        {
            "type": "input",
            "block_id": "amount",
            "label": {"type": "plain_text", "text": t("amount")},
            "element": input_element("value", initial_value=initial_amount),
        },
        {
            "type": "input",
            "block_id": "vendor",
            "label": {"type": "plain_text", "text": t("vendor")},
            "element": input_element("value", initial_value=request.vendor if request else None),
        },
        {
            "type": "input",
            "block_id": "payment_date",
            "label": {"type": "plain_text", "text": t("payment_date")},
            "element": {
                "type": "datepicker",
                "action_id": "value",
                **({"initial_date": request.payment_date.isoformat()} if request else {}),
            },
        },
        {
            "type": "input",
            "block_id": "purpose",
            "label": {"type": "plain_text", "text": t("purpose")},
            "element": input_element(
                "value", initial_value=request.purpose if request else None, multiline=True
            ),
        },
        {
            "type": "input",
            "block_id": "evidence_folder",
            "optional": True,
            "label": {"type": "plain_text", "text": t("evidence_folder")},
            "element": input_element(
                "value",
                initial_value=request.evidence_folder_url if request else None,
                placeholder=t("folder_placeholder"),
            ),
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("sharing_notice")}]},
    ]
    return blocks


def _evidence_fields(
    items: Iterable[EvidenceRequirementDefinition | EvidenceSubmission],
    timing: EvidenceTiming,
) -> list[dict]:
    evidence_heading = t("pre_evidence") if timing == EvidenceTiming.PRE else t("post_evidence")
    blocks: list[dict] = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{evidence_heading}*",
            },
        },
    ]
    for item in items:
        if item.timing != timing:
            continue
        key = getattr(item, "evidence_key", None) or item.requirement_key
        optional = item.requirement == EvidenceRequirementLevel.OPTIONAL
        label_suffix = t("optional") if optional else t("required")
        initial_url = getattr(item, "url", None)
        initial_note = getattr(item, "note", None)
        blocks.append(
            {
                "type": "input",
                "block_id": f"evidence__{key}",
                "optional": optional,
                "label": {
                    "type": "plain_text",
                    "text": f"{item.name_en} / {item.name_ko} ({label_suffix})"[:2000],
                },
                "element": input_element("value", initial_value=initial_url),
            }
        )
        description_en = getattr(item, "description_en", None)
        description_ko = getattr(item, "description_ko", None)
        if description_en or description_ko:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "\n".join(
                                value for value in (description_en, description_ko) if value
                            ),
                        }
                    ],
                }
            )
        blocks.append(
            {
                "type": "input",
                "block_id": f"note__{key}",
                "optional": True,
                "label": {
                    "type": "plain_text",
                    "text": f"{item.name_en} — {t('evidence_note')}"[:2000],
                },
                "element": input_element("value", initial_value=initial_note, multiline=True),
            }
        )
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("sharing_notice")}]}
    )
    return blocks
