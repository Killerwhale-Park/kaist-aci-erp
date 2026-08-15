import json
from collections.abc import Iterable

from app.config.roles import (
    WORKSPACE_ROLE_SCOPE,
    RoleDefinitionSeed,
    empty_role_set,
    role_definitions,
)
from app.domain.enums import ApplicantType, EvidenceRequirementLevel, EvidenceTiming
from app.domain.models import (
    ApplicantProfile,
    BudgetNode,
    Department,
    EvidenceRequirementDefinition,
    EvidenceSubmission,
    ExpenseCategory,
    ExpenseRequest,
    WorkRequest,
)
from app.i18n import display_name, t
from app.slack.messages import request_message_blocks, work_request_blocks
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
            "text": f"{name_ko} / {name_en}"[:75],
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
    profile: ApplicantProfile,
    departments: Iterable[Department],
    budget_nodes: Iterable[BudgetNode],
    category_node_ids: Iterable[str] = (),
    initial_department_id: str | None = None,
    source_work_request_id: str | None = None,
    selected_budget_node_ids: tuple[str, ...] = (),
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
    budget_blocks, _selected_leaf = _budget_selection_blocks(
        budget_nodes, set(category_node_ids), selected_budget_node_ids
    )
    applicant_kind = t(
        "student" if profile.applicant_type == ApplicantType.STUDENT else "professor"
    )
    view = {
        "type": "modal",
        "callback_id": "expense_context",
        "private_metadata": json.dumps(
            {
                "profile": {
                    "slack_user_id": profile.slack_user_id,
                    "applicant_type": profile.applicant_type.value,
                    "applicant_identifier": profile.applicant_identifier,
                },
                **(
                    {"source_work_request_id": source_work_request_id}
                    if source_work_request_id
                    else {}
                ),
            }
        ),
        "title": {"type": "plain_text", "text": t("new_request_short")},
        "submit": {"type": "plain_text", "text": t("continue")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{t('applicant')}*\n<@{profile.slack_user_id}>\n"
                        f"{applicant_kind} · `{profile.applicant_identifier}`\n"
                        "_내 정보 설정에 저장된 값을 사용합니다._"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "department",
                "label": {"type": "plain_text", "text": t("department")},
                "element": department_element,
            },
            *budget_blocks,
        ],
    }
    return view


def applicant_profile_modal(
    slack_user_id: str,
    profile: ApplicantProfile | None = None,
    *,
    applicant_type: ApplicantType | None = None,
    continuation: dict | None = None,
) -> dict:
    selected_type = applicant_type or (
        profile.applicant_type if profile is not None else ApplicantType.STUDENT
    )
    applicant_options = [
        {"text": {"type": "plain_text", "text": t("student")}, "value": "STUDENT"},
        {"text": {"type": "plain_text", "text": t("professor")}, "value": "PROFESSOR"},
    ]
    identifier_key = "student_id" if selected_type == ApplicantType.STUDENT else "employee_id"
    identifier_element = input_element("value")
    if profile is not None and profile.applicant_type == selected_type:
        identifier_element["initial_value"] = profile.applicant_identifier
    return {
        "type": "modal",
        "callback_id": "applicant_profile",
        "private_metadata": json.dumps(
            {
                "slack_user_id": slack_user_id,
                **(
                    {
                        "saved_profile": {
                            "applicant_type": profile.applicant_type.value,
                            "applicant_identifier": profile.applicant_identifier,
                        }
                    }
                    if profile is not None
                    else {}
                ),
                **(continuation or {}),
            }
        ),
        "title": {"type": "plain_text", "text": "내 정보 설정"},
        "submit": {"type": "plain_text", "text": t("save")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "input",
                "block_id": "profile_applicant_type",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": t("applicant_type")},
                "element": {
                    "type": "static_select",
                    "action_id": "profile_applicant_type_changed",
                    "options": applicant_options,
                    "initial_option": next(
                        item for item in applicant_options if item["value"] == selected_type.value
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "profile_identifier",
                "label": {"type": "plain_text", "text": t(identifier_key)},
                "element": identifier_element,
            },
        ],
    }


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
                "filter": {"include": ["private"]},
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


def settlement_request_modal(
    departments: Iterable[Department],
    *,
    source_purchase: WorkRequest | None = None,
) -> dict:
    department_list = list(departments)
    blocks = _work_request_base_blocks(department_list)
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
    if source_purchase is not None:
        selected_department = next(
            (item for item in department_list if item.id == source_purchase.department_id),
            None,
        )
        if selected_department is not None:
            blocks[0]["element"]["initial_option"] = _option(
                selected_department.id,
                selected_department.name_en,
                selected_department.name_ko,
            )
        blocks[2]["element"]["initial_conversation"] = source_purchase.channel_id
        initial_values = {
            "work_subject": source_purchase.subject,
            "work_amount": (
                str(source_purchase.amount) if source_purchase.amount is not None else None
            ),
            "work_purpose": source_purchase.purpose,
        }
        for block in blocks:
            value = initial_values.get(block.get("block_id"))
            if value:
                block["element"]["initial_value"] = value
    return {
        "type": "modal",
        "callback_id": (
            "purchase_settlement_handoff"
            if source_purchase is not None
            else "settlement_request_create"
        ),
        "title": {"type": "plain_text", "text": t("settlement_title")},
        "submit": {"type": "plain_text", "text": t("send_request")},
        "close": {"type": "plain_text", "text": t("cancel")},
        **(
            {"private_metadata": json.dumps({"source_request_id": source_purchase.id})}
            if source_purchase is not None
            else {}
        ),
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


def work_request_details_modal(request: WorkRequest) -> dict:
    return {
        "type": "modal",
        "callback_id": "work_request_details",
        "title": {"type": "plain_text", "text": t("work_requests")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": work_request_blocks(request, include_actions=False),
    }


def work_request_rejection_modal(request_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "work_request_rejection",
        "private_metadata": json.dumps({"request_id": request_id}),
        "title": {"type": "plain_text", "text": t("decision_title")},
        "submit": {"type": "plain_text", "text": t("reject")},
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


def administration_modal() -> dict:
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("admin_menu_notice")},
        },
        {
            "type": "input",
            "block_id": "administration_section",
            "label": {"type": "plain_text", "text": t("setting_to_manage")},
            "element": {
                "type": "static_select",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": t("setting_to_manage")},
                "options": [
                    _option(
                        "approval_procedure",
                        t("configure_rules"),
                        t("configure_rules"),
                    ),
                    _option("access_roles", t("manage_roles"), t("manage_roles")),
                    _option(
                        "system_channels",
                        t("manage_system_channels"),
                        t("manage_system_channels"),
                    ),
                ],
            },
        },
    ]
    return {
        "type": "modal",
        "callback_id": "administration_menu",
        "title": {"type": "plain_text", "text": t("administration_title")},
        "submit": {"type": "plain_text", "text": t("open_setting")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": blocks,
    }


def system_channels_modal(configuration: dict) -> dict:
    def conversation_select(action_id: str, placeholder_key: str, initial: str | None) -> dict:
        element: dict = {
            "type": "conversations_select",
            "action_id": action_id,
            "placeholder": {"type": "plain_text", "text": t(placeholder_key)},
            "filter": {
                "include": ["private"],
                "exclude_external_shared_channels": True,
                "exclude_bot_users": True,
            },
        }
        if initial:
            element["initial_conversation"] = initial
        return element

    additional = configuration.get("additional_operating_channel_ids", [])
    additional_element: dict = {
        "type": "multi_conversations_select",
        "action_id": "value",
        "placeholder": {"type": "plain_text", "text": t("additional_operating_channels")},
        "filter": {
            "include": ["private"],
            "exclude_external_shared_channels": True,
            "exclude_bot_users": True,
        },
    }
    if additional:
        additional_element["initial_conversations"] = additional
    return {
        "type": "modal",
        "callback_id": "system_channels_editor",
        "title": {"type": "plain_text", "text": t("system_channels_title")},
        "submit": {"type": "plain_text", "text": t("save")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": t("system_channels_notice")}],
            },
            {
                "type": "input",
                "block_id": "audit_channel",
                "label": {"type": "plain_text", "text": t("audit_channel")},
                "element": conversation_select(
                    "value", "audit_channel", configuration.get("audit_channel_id")
                ),
            },
            {
                "type": "input",
                "block_id": "alerts_channel",
                "label": {"type": "plain_text", "text": t("alerts_channel")},
                "element": conversation_select(
                    "value", "alerts_channel", configuration.get("alerts_channel_id")
                ),
            },
            {
                "type": "input",
                "block_id": "additional_operating_channels",
                "optional": True,
                "label": {
                    "type": "plain_text",
                    "text": t("additional_operating_channels"),
                },
                "element": additional_element,
            },
        ],
    }


def configuration_notice_modal(message: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "configuration_notice",
        "title": {"type": "plain_text", "text": t("status_title")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            }
        ],
    }


def loading_modal(message: str | None = None) -> dict:
    return {
        "type": "modal",
        "callback_id": "loading",
        "title": {"type": "plain_text", "text": t("loading_title")},
        "close": {"type": "plain_text", "text": t("close")},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message or t("loading")},
            }
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
    workflow_name_en: str,
    workflow_name_ko: str,
    steps: list[dict],
    assigned_user_ids_by_role: dict[str, tuple[str, ...] | list[str]] | None = None,
) -> dict:
    assigned_user_ids_by_role = assigned_user_ids_by_role or {}
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
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{t('workflow')}*\n{display_name(workflow_name_en, workflow_name_ko)}",
            },
        },
        {
            "type": "input",
            "block_id": "approval_channel",
            "label": {"type": "plain_text", "text": t("approval_channel")},
            "element": channel_element,
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": t("approval_channel_notice"),
                }
            ],
        },
    ]
    role_names = {role.id: role.name_ko for role in role_definitions()}
    role_order: list[str] = []
    for index, step in enumerate(steps):
        for role_id in step.get("approver_roles") or []:
            if role_id not in role_order:
                role_order.append(role_id)
        roles = (
            ", ".join(
                role_names.get(role_id, role_id) for role_id in (step.get("approver_roles") or [])
            )
            or "-"
        )
        step_name = display_name(str(step.get("name_en") or ""), str(step.get("name_ko") or ""))
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{index + 1}단계 · {step_name}*\n{t('required_roles')}: {roles}"
                        ),
                    },
                },
            ]
        )
    if role_order:
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": t("approval_assignee_notice")},
                },
            ]
        )
    for role_id in role_order:
        selected = sorted(set(assigned_user_ids_by_role.get(role_id, ())))
        element: dict = {
            "type": "multi_users_select",
            "action_id": "value",
            "placeholder": {
                "type": "plain_text",
                "text": t("select_role_assignees", role=role_names.get(role_id, role_id)),
            },
        }
        if selected:
            element["initial_users"] = selected
        blocks.append(
            {
                "type": "input",
                "block_id": f"approval_role__{role_id}",
                "label": {
                    "type": "plain_text",
                    "text": t("role_assignees", role=role_names.get(role_id, role_id)),
                },
                "element": element,
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
        "blocks": blocks,
    }


def role_configuration_modal(assignments: dict[str, dict[str, set[str]]]) -> dict:
    blocks: list[dict] = [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": t("role_configuration_notice")}],
        }
    ]

    def append_role_field(role: RoleDefinitionSeed) -> None:
        selected = sorted(
            assignments.get(WORKSPACE_ROLE_SCOPE, empty_role_set()).get(role.id, set())
        )
        label = display_name(role.name_en, role.name_ko)
        element: dict = {
            "type": "multi_users_select",
            "action_id": "value",
            "placeholder": {"type": "plain_text", "text": label[:150]},
        }
        if selected:
            element["initial_users"] = selected
        blocks.append(
            {
                "type": "input",
                "block_id": f"access_role__{WORKSPACE_ROLE_SCOPE}__{role.id}",
                "optional": not role.required,
                "label": {"type": "plain_text", "text": label},
                "element": element,
            }
        )

    blocks.append(
        {
            "type": "header",
            "text": {"type": "plain_text", "text": t("workspace_roles")},
        }
    )
    for role in role_definitions():
        append_role_field(role)
    return {
        "type": "modal",
        "callback_id": "access_roles_editor",
        "title": {"type": "plain_text", "text": t("roles_title")},
        "submit": {"type": "plain_text", "text": t("save")},
        "close": {"type": "plain_text", "text": t("cancel")},
        "blocks": blocks,
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
