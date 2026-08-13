import json
from collections.abc import Iterable

from app.domain.enums import ApplicantType, EvidenceRequirementLevel, EvidenceTiming
from app.domain.models import (
    BudgetNode,
    Department,
    EvidenceRequirementDefinition,
    EvidenceSubmission,
    ExpenseCategory,
    ExpenseRequest,
    WorkRequest,
)
from app.i18n import display_name, t
from app.slack.messages import request_message_blocks
from app.slack.utils import input_element


def _option(value: str, name_en: str, name_ko: str) -> dict:
    return {
        "text": {"type": "plain_text", "text": display_name(name_en, name_ko)[:75]},
        "value": value,
    }


def _category_option(category: ExpenseCategory) -> dict:
    name_en = " / ".join(category.budget_path_en) or category.name_en
    name_ko = " / ".join(category.budget_path_ko) or category.name_ko
    return {
        "text": {
            "type": "plain_text",
            "text": (f"{name_ko} → {category.form_name_ko} / {name_en} → {category.form_name_en}")[
                :75
            ],
        },
        "value": category.id,
    }


def _budget_selection_blocks(
    nodes: Iterable[BudgetNode],
    category_node_ids: set[str],
    selected_node_ids: tuple[str, ...],
) -> tuple[list[dict], BudgetNode | None]:
    node_list = list(nodes)
    by_parent: dict[str | None, list[BudgetNode]] = {}
    for node in node_list:
        by_parent.setdefault(node.parent_id, []).append(node)

    blocks: list[dict] = []
    parent_id: str | None = None
    selected_leaf: BudgetNode | None = None
    for level in range(1, len(node_list) + 1):
        choices = by_parent.get(parent_id, [])
        if not choices:
            break
        options = [_option(item.id, item.name_en, item.name_ko) for item in choices]
        selected_id = selected_node_ids[level - 1] if level <= len(selected_node_ids) else None
        selected = next((item for item in choices if item.id == selected_id), None)
        element: dict = {
            "type": "static_select",
            "action_id": "budget_node_selected",
            "options": options,
        }
        initial_option = next(
            (item for item in options if selected and item["value"] == selected.id), None
        )
        if initial_option:
            element["initial_option"] = initial_option
        is_category_level = all(item.id in category_node_ids for item in choices)
        blocks.append(
            {
                "type": "input",
                "block_id": f"budget_level_{level}",
                "dispatch_action": True,
                "label": {
                    "type": "plain_text",
                    "text": (
                        t("expense_category")
                        if is_category_level
                        else t("budget_level", level=level)
                    ),
                },
                "element": element,
            }
        )
        if selected is None:
            break
        if selected.id in category_node_ids:
            selected_leaf = selected
            break
        parent_id = selected.id
    return blocks, selected_leaf


def expense_context_modal(
    slack_user_id: str,
    departments: Iterable[Department],
    budget_nodes: Iterable[BudgetNode],
    category_node_ids: Iterable[str] = (),
    initial_department_id: str | None = None,
    source_work_request_id: str | None = None,
    selected_budget_node_ids: tuple[str, ...] = (),
    applicant_type: ApplicantType = ApplicantType.STUDENT,
) -> dict:
    department_options = [_option(item.id, item.name_en, item.name_ko) for item in departments]
    department_element: dict = {
        "type": "static_select",
        "action_id": "value",
        "options": department_options,
    }
    initial_department = next(
        (item for item in department_options if item["value"] == initial_department_id), None
    )
    if initial_department is not None:
        department_element["initial_option"] = initial_department
    applicant_options = [
        {"text": {"type": "plain_text", "text": t("student")}, "value": "STUDENT"},
        {"text": {"type": "plain_text", "text": t("professor")}, "value": "PROFESSOR"},
    ]
    applicant_element = {
        "type": "static_select",
        "action_id": "applicant_type_changed",
        "options": applicant_options,
        "initial_option": next(
            item for item in applicant_options if item["value"] == applicant_type.value
        ),
    }
    budget_blocks, _selected_leaf = _budget_selection_blocks(
        budget_nodes, set(category_node_ids), selected_budget_node_ids
    )
    identifier_block_id = (
        "student_number" if applicant_type == ApplicantType.STUDENT else "employee_number"
    )
    view = {
        "type": "modal",
        "callback_id": "expense_context",
        "private_metadata": json.dumps(
            {"source_work_request_id": source_work_request_id} if source_work_request_id else {}
        ),
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
                "element": department_element,
            },
            {
                "type": "input",
                "block_id": "applicant_type",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": t("applicant_type")},
                "element": applicant_element,
            },
            {
                "type": "input",
                "block_id": identifier_block_id,
                "label": {
                    "type": "plain_text",
                    "text": (
                        t("student_id")
                        if applicant_type == ApplicantType.STUDENT
                        else t("employee_id")
                    ),
                },
                "element": input_element("value"),
            },
            *budget_blocks,
        ],
    }
    return view


def _work_request_base_blocks(departments: Iterable[Department]) -> list[dict]:
    return [
        {
            "type": "input",
            "block_id": "work_department",
            "label": {"type": "plain_text", "text": t("department")},
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": [_option(item.id, item.name_en, item.name_ko) for item in departments],
            },
        },
        {
            "type": "input",
            "block_id": "work_channel",
            "label": {"type": "plain_text", "text": t("request_channel")},
            "element": {
                "type": "conversations_select",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": t("select_channel")},
                "filter": {
                    "include": ["private"],
                    "exclude_external_shared_channels": True,
                    "exclude_bot_users": True,
                },
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": t("work_channel_notice")}],
        },
    ]


def purchase_request_modal(departments: Iterable[Department]) -> dict:
    blocks = _work_request_base_blocks(departments)
    blocks[1:1] = [
        {
            "type": "input",
            "block_id": "purchase_assignee",
            "label": {"type": "plain_text", "text": t("purchase_assignee")},
            "element": {
                "type": "users_select",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": t("select_person")},
            },
        }
    ]
    blocks.extend(
        [
            {
                "type": "input",
                "block_id": "item_name",
                "label": {"type": "plain_text", "text": t("item_name")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "product_url",
                "label": {"type": "plain_text", "text": t("product_url")},
                "element": input_element("value", placeholder="https://..."),
            },
            {
                "type": "input",
                "block_id": "quantity",
                "label": {"type": "plain_text", "text": t("quantity")},
                "element": input_element("value", initial_value="1"),
            },
            {
                "type": "input",
                "block_id": "estimated_amount",
                "optional": True,
                "label": {"type": "plain_text", "text": t("estimated_amount")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "work_purpose",
                "label": {"type": "plain_text", "text": t("purpose")},
                "element": input_element("value", multiline=True),
            },
        ]
    )
    return {
        "type": "modal",
        "callback_id": "purchase_request_create",
        "title": {"type": "plain_text", "text": t("purchase_title")},
        "submit": {"type": "plain_text", "text": t("send_request")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": blocks,
    }


def settlement_request_modal(departments: Iterable[Department]) -> dict:
    blocks = _work_request_base_blocks(departments)
    blocks[1:1] = [
        {
            "type": "input",
            "block_id": "settlement_assignee",
            "label": {"type": "plain_text", "text": t("settlement_assignee")},
            "element": {
                "type": "users_select",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": t("select_student")},
            },
        }
    ]
    blocks.extend(
        [
            {
                "type": "input",
                "block_id": "work_subject",
                "label": {"type": "plain_text", "text": t("purchase_subject")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "work_vendor",
                "label": {"type": "plain_text", "text": t("vendor")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "work_amount",
                "label": {"type": "plain_text", "text": t("amount")},
                "element": input_element("value"),
            },
            {
                "type": "input",
                "block_id": "work_payment_date",
                "label": {"type": "plain_text", "text": t("payment_date")},
                "element": {"type": "datepicker", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "work_purpose",
                "label": {"type": "plain_text", "text": t("purpose")},
                "element": input_element("value", multiline=True),
            },
            {
                "type": "input",
                "block_id": "work_evidence_folder",
                "optional": True,
                "label": {"type": "plain_text", "text": t("evidence_folder")},
                "element": input_element("value", placeholder=t("folder_placeholder")),
            },
        ]
    )
    return {
        "type": "modal",
        "callback_id": "settlement_request_create",
        "title": {"type": "plain_text", "text": t("settlement_title")},
        "submit": {"type": "plain_text", "text": t("send_request")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": blocks,
    }


def expense_details_modal(
    context: dict,
    requirements: list[EvidenceRequirementDefinition],
    initial_request: WorkRequest | None = None,
) -> dict:
    return {
        "type": "modal",
        "callback_id": "expense_details",
        "private_metadata": json.dumps(context),
        "title": {"type": "plain_text", "text": t("details_title")},
        "submit": {"type": "plain_text", "text": t("submit")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": _expense_fields(initial_request)
        + _evidence_fields(requirements, EvidenceTiming.PRE),
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
        "callback_id": "administration_menu",
        "title": {"type": "plain_text", "text": t("administration_title")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("admin_menu_notice")},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "configure_approval_rules",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": t("configure_rules")},
                        "value": "rules",
                    },
                    {
                        "type": "button",
                        "action_id": "configure_system_admins",
                        "text": {"type": "plain_text", "text": t("manage_admins")},
                        "value": "admins",
                    },
                ],
            },
        ],
    }


def approval_rule_selector_modal(
    departments: Iterable[Department], categories: Iterable[ExpenseCategory]
) -> dict:
    return {
        "type": "modal",
        "callback_id": "approval_rule_selector",
        "title": {"type": "plain_text", "text": t("select_rule")},
        "submit": {"type": "plain_text", "text": t("continue")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "input",
                "block_id": "rule_department",
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
                "block_id": "rule_category",
                "label": {"type": "plain_text", "text": t("expense_category")},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": [_category_option(item) for item in categories],
                },
            },
        ],
    }


def approval_rule_editor_modal(
    department_id: str,
    category_id: str,
    approval_channel_id: str | None,
    steps: list[dict],
) -> dict:
    channel_element: dict = {
        "type": "conversations_select",
        "action_id": "value",
        "placeholder": {"type": "plain_text", "text": t("approval_channel")},
        "filter": {
            "include": ["private"],
            "exclude_external_shared_channels": True,
            "exclude_bot_users": True,
        },
    }
    if approval_channel_id:
        channel_element["initial_conversation"] = approval_channel_id

    blocks: list[dict] = [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": t("any_approver_notice")}],
        },
        {
            "type": "input",
            "block_id": "approval_channel",
            "label": {"type": "plain_text", "text": t("approval_channel")},
            "element": channel_element,
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": t("private_channel_only")}],
        },
    ]
    for index, step in enumerate(steps):
        approver_element: dict = {
            "type": "multi_users_select",
            "action_id": "value",
            "placeholder": {"type": "plain_text", "text": t("step_approvers")},
        }
        approver_ids = list(step.get("approver_slack_user_ids") or [])
        if approver_ids:
            approver_element["initial_users"] = approver_ids
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Step {index + 1}*"},
                    "accessory": {
                        "type": "button",
                        "action_id": "remove_approval_step",
                        "text": {"type": "plain_text", "text": t("remove_step")},
                        "value": str(index),
                    },
                },
                {
                    "type": "input",
                    "block_id": f"step_name_en__{index}",
                    "label": {"type": "plain_text", "text": t("step_name_en")},
                    "element": input_element("value", initial_value=str(step.get("name_en") or "")),
                },
                {
                    "type": "input",
                    "block_id": f"step_name_ko__{index}",
                    "label": {"type": "plain_text", "text": t("step_name_ko")},
                    "element": input_element("value", initial_value=str(step.get("name_ko") or "")),
                },
                {
                    "type": "input",
                    "block_id": f"step_approvers__{index}",
                    "optional": True,
                    "label": {"type": "plain_text", "text": t("step_approvers")},
                    "element": approver_element,
                },
            ]
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "add_approval_step",
                    "text": {"type": "plain_text", "text": t("add_step")},
                    "value": "add",
                }
            ],
        }
    )
    return {
        "type": "modal",
        "callback_id": "approval_rule_editor",
        "private_metadata": json.dumps(
            {"department_id": department_id, "category_id": category_id}
        ),
        "title": {"type": "plain_text", "text": t("approval_rule_title")},
        "submit": {"type": "plain_text", "text": t("save")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": blocks[:100],
    }


def system_admins_modal(admin_slack_user_ids: list[str]) -> dict:
    element: dict = {
        "type": "multi_users_select",
        "action_id": "value",
        "placeholder": {"type": "plain_text", "text": t("system_admins")},
    }
    if admin_slack_user_ids:
        element["initial_users"] = admin_slack_user_ids
    return {
        "type": "modal",
        "callback_id": "system_admins_editor",
        "title": {"type": "plain_text", "text": t("admin_title")},
        "submit": {"type": "plain_text", "text": t("save")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "input",
                "block_id": "system_admins",
                "label": {"type": "plain_text", "text": t("system_admins")},
                "element": element,
            }
        ],
    }


def _expense_fields(request: ExpenseRequest | WorkRequest | None = None) -> list[dict]:
    initial_amount = None
    if request is not None and request.amount is not None:
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
                **(
                    {"initial_date": request.payment_date.isoformat()}
                    if request and request.payment_date
                    else {}
                ),
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
