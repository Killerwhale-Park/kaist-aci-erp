from dataclasses import dataclass

from app.domain.enums import BudgetFormScope

LEGACY_BUDGET_IDS = {"student_support": "department_budget"}


@dataclass(frozen=True)
class BudgetNodeSeed:
    id: str
    parent_id: str | None
    name_en: str
    name_ko: str
    form_scope: BudgetFormScope | None = None


BUDGET_NODE_SEEDS = [
    BudgetNodeSeed(
        "department_budget",
        None,
        "Department Budget",
        "학과예산",
        BudgetFormScope.DEPARTMENT,
    ),
    BudgetNodeSeed(
        "academic_development",
        "department_budget",
        "Academic Development Fund",
        "학사계발비",
    ),
    BudgetNodeSeed("supplies", "academic_development", "Supplies", "비품비"),
]
