from app.domain.enums import ApplicantType, EvidenceRequirementLevel, EvidenceTiming
from app.domain.models import (
    BudgetNode,
    Department,
    EvidenceRequirementDefinition,
)
from app.slack.modals import (
    approval_rule_editor_modal,
    expense_context_modal,
    expense_details_modal,
    system_admins_modal,
)


def test_context_and_dynamic_evidence_modal_use_block_kit_limits() -> None:
    context_view = expense_context_modal(
        "U_STUDENT",
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
            BudgetNode("supplies", "academic_development", "Supplies", "비품비", "supplies"),
        ],
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


def test_budget_depth_and_applicant_identifier_are_dynamic() -> None:
    department = Department("department_1", "AI Computing", "AI Computing")
    nodes = [
        BudgetNode("l1", None, "Level 1", "1단계"),
        BudgetNode("l2", "l1", "Level 2", "2단계"),
        BudgetNode("l3", "l2", "Level 3", "3단계"),
        BudgetNode("l4", "l3", "Level 4", "4단계"),
        BudgetNode("l5", "l4", "Leaf", "최종 항목", "leaf_category"),
    ]
    incomplete = expense_context_modal(
        "U_USER", [department], nodes, selected_budget_node_ids=("l1",)
    )
    professor = expense_context_modal(
        "U_USER",
        [department],
        nodes,
        selected_budget_node_ids=("l1", "l2", "l3", "l4", "l5"),
        applicant_type=ApplicantType.PROFESSOR,
    )

    assert "submit" not in incomplete
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
    assert any(block.get("block_id") == "employee_number" for block in professor["blocks"])
    assert not any(block.get("block_id") == "student_number" for block in professor["blocks"])

    two_level_nodes = [
        BudgetNode("root", None, "Root", "상위"),
        BudgetNode("leaf", "root", "Leaf", "최종", "leaf_category"),
    ]
    two_level = expense_context_modal(
        "U_USER",
        [department],
        two_level_nodes,
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


def test_runtime_configuration_modals_use_native_slack_selectors() -> None:
    editor = approval_rule_editor_modal(
        "department_1",
        "supplies",
        "C_PRIVATE",
        [
            {
                "name_en": "Professor Approval",
                "name_ko": "교수 승인",
                "approver_slack_user_ids": ["U_PROFESSOR_A", "U_PROFESSOR_B"],
            }
        ],
    )
    admins = system_admins_modal(["U_ADMIN_A", "U_ADMIN_B"])

    editor_elements = [block.get("element") for block in editor["blocks"] if block.get("element")]
    assert any(element["type"] == "conversations_select" for element in editor_elements)
    assert any(element["type"] == "multi_users_select" for element in editor_elements)
    assert admins["blocks"][0]["element"]["type"] == "multi_users_select"
    assert len(editor["blocks"]) <= 100
