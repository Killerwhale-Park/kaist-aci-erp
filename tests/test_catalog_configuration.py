import pytest

from app.config.roles import ADMIN_STAFF_ROLE, PROFESSOR_ROLE, STUDENT_COORDINATOR_ROLE
from app.domain.catalog import (
    budget_path,
    budgets,
    categories,
    departments,
    resolve_expense_categories,
    workflow_for_budget_node,
)
from app.domain.enums import BudgetFormScope
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


def test_department_scoped_forms_do_not_affect_college_budget_forms() -> None:
    nodes = [
        BudgetNode(
            "department_budget",
            None,
            "Department Budget",
            "학과별 예산",
            BudgetFormScope.DEPARTMENT,
        ),
        BudgetNode("department_airfare", "department_budget", "Airfare", "항공료"),
        BudgetNode(
            "student_council_budget",
            None,
            "Department Student Council Budget",
            "학과 학생회 예산",
            BudgetFormScope.DEPARTMENT,
        ),
        BudgetNode("council_airfare", "student_council_budget", "Airfare", "항공료"),
        BudgetNode(
            "college_budget",
            None,
            "College Budget",
            "단과대 예산",
            BudgetFormScope.GLOBAL,
        ),
        BudgetNode("college_airfare", "college_budget", "Airfare", "항공료"),
    ]
    forms = [
        ExpenseForm("airfare_default", "Default Airfare", "항공료 기본"),
        ExpenseForm("airfare_aic", "AIC Airfare", "AIC 항공료"),
    ]
    mappings = [
        BudgetFormMapping("department_airfare", "airfare_default"),
        BudgetFormMapping("department_airfare", "airfare_aic", "department_1"),
        BudgetFormMapping("council_airfare", "airfare_default"),
        BudgetFormMapping("council_airfare", "airfare_aic", "department_1"),
        BudgetFormMapping("college_airfare", "airfare_default"),
    ]

    department_1 = resolve_expense_categories(nodes, forms, mappings, "department_1")
    department_2 = resolve_expense_categories(nodes, forms, mappings, "department_2")
    assert {item.id: item.form_id for item in department_1} == {
        "department_airfare": "airfare_aic",
        "council_airfare": "airfare_aic",
        "college_airfare": "airfare_default",
    }
    assert {item.id: item.form_id for item in department_2} == {
        "department_airfare": "airfare_default",
        "council_airfare": "airfare_default",
        "college_airfare": "airfare_default",
    }

    with pytest.raises(ValueError, match="Global budget programs"):
        resolve_expense_categories(
            nodes,
            forms,
            [BudgetFormMapping("college_airfare", "airfare_aic", "department_1")],
            "department_1",
        )


def test_academic_development_workflow_is_role_based_and_n_step() -> None:
    workflow = workflow_for_budget_node("supplies", "department_1")

    assert workflow is not None
    assert workflow.id == "academic_development_approval"
    assert [step.approver_roles for step in workflow.steps] == [
        (STUDENT_COORDINATOR_ROLE,),
        (PROFESSOR_ROLE,),
        (ADMIN_STAFF_ROLE,),
    ]
