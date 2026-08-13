from app.domain.catalog import (
    budget_path,
    budgets,
    categories,
    departments,
    resolve_expense_categories,
)
from app.domain.models import BudgetFormMapping, BudgetNode, ExpenseForm


def test_operational_department_and_budget_names() -> None:
    assert [(item.id, item.name_en) for item in departments()] == [
        ("department_1", "AI Computing"),
        ("department_2", "AI System"),
        ("department_3", "AX"),
        ("department_4", "AI Future"),
    ]
    assert [(item.id, item.name_ko, item.name_en, item.is_available) for item in budgets()] == [
        ("department_budget", "학과예산", "Department Budget", True),
    ]
    assert [item.name_ko for item in budget_path("supplies")] == [
        "학과예산",
        "학사계발비",
        "비품비",
    ]
    assert categories()[0].budget_path_en == (
        "Department Budget",
        "Academic Development Fund",
        "Supplies",
    )
    assert categories()[0].form_id == "supplies_settlement"


def test_budget_items_and_forms_are_independent_axes() -> None:
    nodes = [
        BudgetNode("department", None, "Department Budget", "학과예산"),
        BudgetNode("department_airfare", "department", "Airfare", "항공료"),
        BudgetNode("student_union", None, "Student Union", "학생자치"),
        BudgetNode("union_airfare", "student_union", "Airfare", "항공료"),
        BudgetNode("other", None, "Other", "기타"),
        BudgetNode("other_airfare", "other", "Airfare", "항공료"),
    ]
    forms = [
        ExpenseForm("airfare_settlement", "Airfare Settlement", "항공료 정산"),
        ExpenseForm("union_nonsettlement", "Union Non-settlement", "자치 비정산"),
    ]
    resolved = resolve_expense_categories(
        nodes,
        forms,
        [
            BudgetFormMapping("department_airfare", "airfare_settlement"),
            BudgetFormMapping("union_airfare", "union_nonsettlement"),
            BudgetFormMapping("other_airfare", "airfare_settlement"),
        ],
    )

    assert {item.id: item.form_id for item in resolved} == {
        "department_airfare": "airfare_settlement",
        "union_airfare": "union_nonsettlement",
        "other_airfare": "airfare_settlement",
    }
