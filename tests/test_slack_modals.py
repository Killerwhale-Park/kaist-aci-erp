from app.db.enums import EvidenceRequirementLevel, EvidenceTiming
from app.db.models import (
    BudgetProgram,
    Department,
    EvidenceRequirementDefinition,
    ExpenseCategory,
)
from app.slack.modals import expense_context_modal, expense_details_modal


def test_context_and_dynamic_evidence_modal_use_block_kit_limits() -> None:
    context_view = expense_context_modal(
        "U_STUDENT",
        [
            Department(
                id=f"department_{index}",
                name_en=f"Department {index}",
                name_ko=f"학과 {index}",
                approval_channel_id=f"C_DEPARTMENT_{index}",
            )
            for index in range(1, 5)
        ],
        [
            BudgetProgram(
                id="student_support",
                name_en="Student Support Budget",
                name_ko="학생 지원 예산",
                is_available=True,
            )
        ],
        [
            ExpenseCategory(
                id="airfare",
                budget_program_id="student_support",
                name_en="Airfare",
                name_ko="항공료",
            )
        ],
    )
    details_view = expense_details_modal(
        {
            "department_id": "department_1",
            "applicant_type": "STUDENT",
            "student_id": "202500001",
            "budget_program_id": "student_support",
            "category_id": "airfare",
        },
        [
            EvidenceRequirementDefinition(
                id="airfare.card_receipt",
                category_id="airfare",
                evidence_key="card_receipt",
                name_en="Card Receipt",
                name_ko="카드 영수증",
                timing=EvidenceTiming.PRE,
                requirement=EvidenceRequirementLevel.REQUIRED,
                display_order=1,
            ),
            EvidenceRequirementDefinition(
                id="airfare.e_ticket",
                category_id="airfare",
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
