from app.config.roles import (
    ADMIN_STAFF_ROLE,
    PROFESSOR_ROLE,
    SYSTEM_ADMIN_ROLE,
    empty_role_set,
)
from app.domain.enums import ApplicantType, EvidenceRequirementLevel, EvidenceTiming
from app.domain.models import (
    ApplicantProfile,
    BudgetNode,
    Department,
    EvidenceRequirementDefinition,
)
from app.slack.modals import (
    applicant_profile_modal,
    approval_rule_editor_modal,
    configuration_notice_modal,
    expense_context_modal,
    expense_details_modal,
    loading_modal,
    role_configuration_modal,
    system_channels_modal,
)
from app.slack.surfaces import validated_view


def test_context_and_dynamic_evidence_modal_use_block_kit_limits() -> None:
    context_view = expense_context_modal(
        ApplicantProfile("U_STUDENT", ApplicantType.STUDENT, "202500001"),
        [
            Department(
                id=f"department_{index}",
                name_en=f"Department {index}",
                name_ko=f"학과 {index}",
            )
            for index in range(1, 5)
        ],
        [
            BudgetNode("department_budget", None, "Department Budget", "학과예산"),
            BudgetNode(
                "academic_development",
                "department_budget",
                "Academic Development Fund",
                "학사계발비",
            ),
            BudgetNode("supplies", "academic_development", "Supplies", "비품비"),
        ],
        category_node_ids={"supplies"},
        selected_budget_node_ids=(
            "department_budget",
            "academic_development",
            "supplies",
        ),
    )
    details_view = expense_details_modal(
        {
            "department_id": "department_1",
            "applicant_type": "STUDENT",
            "applicant_identifier": "202500001",
            "budget_program_id": "department_budget",
            "category_id": "supplies",
        },
        [
            EvidenceRequirementDefinition(
                id="supplies.card_receipt",
                category_id="supplies",
                evidence_key="card_receipt",
                name_en="Card Receipt",
                name_ko="카드 영수증",
                timing=EvidenceTiming.PRE,
                requirement=EvidenceRequirementLevel.REQUIRED,
                display_order=1,
            ),
            EvidenceRequirementDefinition(
                id="supplies.e_ticket",
                category_id="supplies",
                evidence_key="e_ticket",
                name_en="E-ticket PDF",
                name_ko="E-ticket PDF",
                timing=EvidenceTiming.PRE,
                requirement=EvidenceRequirementLevel.OPTIONAL,
                display_order=2,
            ),
        ],
    )

    for view in (context_view, details_view):
        assert view["type"] == "modal"
        assert len(view["title"]["text"]) <= 24
        assert len(view["blocks"]) <= 100

    evidence_blocks = {
        block["block_id"]: block
        for block in details_view["blocks"]
        if block.get("block_id", "").startswith("evidence__")
    }
    assert evidence_blocks["evidence__card_receipt"].get("optional", False) is False
    assert evidence_blocks["evidence__e_ticket"]["optional"] is True


def test_budget_depth_is_dynamic_and_profile_identity_is_reused() -> None:
    department = Department("department_1", "AI Computing", "AI Computing")
    nodes = [
        BudgetNode("l1", None, "Level 1", "1단계"),
        BudgetNode("l2", "l1", "Level 2", "2단계"),
        BudgetNode("l3", "l2", "Level 3", "3단계"),
        BudgetNode("l4", "l3", "Level 4", "4단계"),
        BudgetNode("l5", "l4", "Leaf", "최종 항목"),
    ]
    incomplete = expense_context_modal(
        ApplicantProfile("U_USER", ApplicantType.STUDENT, "202500001"),
        [department],
        nodes,
        category_node_ids={"l5"},
        selected_budget_node_ids=("l1",),
    )
    professor = expense_context_modal(
        ApplicantProfile("U_USER", ApplicantType.PROFESSOR, "E12345"),
        [department],
        nodes,
        category_node_ids={"l5"},
        selected_budget_node_ids=("l1", "l2", "l3", "l4", "l5"),
    )

    assert "submit" in incomplete
    assert "submit" in professor
    assert (
        len(
            [
                block
                for block in professor["blocks"]
                if block.get("block_id", "").startswith("budget_level_")
            ]
        )
        == 5
    )
    assert not any(
        block.get("block_id") in {"employee_number", "student_number", "applicant_type"}
        for block in professor["blocks"]
    )
    assert any("E12345" in block.get("text", {}).get("text", "") for block in professor["blocks"])

    two_level_nodes = [
        BudgetNode("root", None, "Root", "상위"),
        BudgetNode("leaf", "root", "Leaf", "최종"),
    ]
    two_level = expense_context_modal(
        ApplicantProfile("U_USER", ApplicantType.STUDENT, "202500001"),
        [department],
        two_level_nodes,
        category_node_ids={"leaf"},
        selected_budget_node_ids=("root", "leaf"),
    )
    assert (
        len(
            [
                block
                for block in two_level["blocks"]
                if block.get("block_id", "").startswith("budget_level_")
            ]
        )
        == 2
    )


def test_applicant_profile_form_switches_between_student_and_employee_id() -> None:
    student = applicant_profile_modal(
        "U_USER",
        ApplicantProfile("U_USER", ApplicantType.STUDENT, "202600001"),
    )
    professor = applicant_profile_modal(
        "U_USER",
        ApplicantProfile("U_USER", ApplicantType.PROFESSOR, "E12345"),
    )

    student_identifier = next(
        block for block in student["blocks"] if block["block_id"] == "profile_identifier"
    )
    professor_identifier = next(
        block for block in professor["blocks"] if block["block_id"] == "profile_identifier"
    )
    assert "학번" in student_identifier["label"]["text"]
    assert student_identifier["element"]["initial_value"] == "202600001"
    assert "사번" in professor_identifier["label"]["text"]
    assert professor_identifier["element"]["initial_value"] == "E12345"


def test_runtime_configuration_modals_use_native_slack_selectors() -> None:
    editor = approval_rule_editor_modal(
        "department_1",
        "supplies",
        "C_PRIVATE",
        "Academic Development Approval",
        "학사계발비 승인",
        [
            {
                "name_en": "Professor Approval",
                "name_ko": "교수 승인",
                "approver_slack_user_ids": ["U_PROFESSOR_A", "U_PROFESSOR_B"],
                "approver_roles": [PROFESSOR_ROLE],
            }
        ],
        {PROFESSOR_ROLE: ("U_PROFESSOR_A", "U_PROFESSOR_B")},
    )
    roles = role_configuration_modal(
        {
            "workspace": {
                SYSTEM_ADMIN_ROLE: {"U_ADMIN_A", "U_ADMIN_B"},
                PROFESSOR_ROLE: {"U_PROFESSOR_A", "U_PROFESSOR_B"},
                ADMIN_STAFF_ROLE: {"U_STAFF"},
            },
        }
    )
    channels = system_channels_modal(
        {
            "audit_channel_id": "C_AUDIT",
            "alerts_channel_id": "C_ALERTS",
            "additional_operating_channel_ids": ["C_WORK"],
        }
    )

    editor_elements = [block.get("element") for block in editor["blocks"] if block.get("element")]
    assert any(element["type"] == "conversations_select" for element in editor_elements)
    approver_input = next(
        block
        for block in editor["blocks"]
        if block.get("block_id") == f"approval_role__{PROFESSOR_ROLE}"
    )
    assert approver_input["element"]["type"] == "multi_users_select"
    assert approver_input["element"]["initial_users"] == [
        "U_PROFESSOR_A",
        "U_PROFESSOR_B",
    ]
    validated_view(editor)
    role_inputs = [block for block in roles["blocks"] if block["type"] == "input"]
    assert len(role_inputs) == 5
    assert all(block["element"]["type"] == "multi_users_select" for block in role_inputs)
    assert [block["element"]["type"] for block in channels["blocks"] if "element" in block] == [
        "conversations_select",
        "conversations_select",
        "multi_conversations_select",
    ]
    assert len(editor["blocks"]) <= 100


def test_every_modal_with_input_blocks_has_a_submit_button() -> None:
    department = Department("department_1", "AI Computing", "AI Computing")
    views = [
        expense_context_modal(
            ApplicantProfile("U_USER", ApplicantType.STUDENT, "202500001"),
            [department],
            [BudgetNode("root", None, "Root", "상위")],
        ),
        approval_rule_editor_modal(
            "department_1",
            "supplies",
            None,
            "Approval Workflow",
            "승인 절차",
            [
                {
                    "name_en": "Approval",
                    "name_ko": "승인",
                    "approver_slack_user_ids": [],
                    "approver_roles": [PROFESSOR_ROLE],
                }
            ],
        ),
        role_configuration_modal({"workspace": empty_role_set()}),
        system_channels_modal({}),
    ]
    for view in views:
        if any(block.get("type") == "input" for block in view["blocks"]):
            assert view.get("submit"), view["callback_id"]


def test_configuration_notice_view_does_not_require_input() -> None:
    notice = configuration_notice_modal("Unable to load / 불러오기 실패")

    assert notice["callback_id"] == "configuration_notice"
    assert all(block["type"] != "input" for block in notice["blocks"])

    loading = loading_modal()
    assert loading["callback_id"] == "loading"
    assert all(block["type"] != "input" for block in loading["blocks"])
