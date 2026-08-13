from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetSeed:
    id: str
    name_en: str
    name_ko: str
    is_available: bool


BUDGET_SEEDS = [
    BudgetSeed("department_budget", "Department Budget", "학과예산", True),
]

LEGACY_BUDGET_IDS = {"student_support": "department_budget"}


@dataclass(frozen=True)
class BudgetNodeSeed:
    id: str
    parent_id: str | None
    name_en: str
    name_ko: str
    expense_category_id: str | None = None


BUDGET_NODE_SEEDS = [
    BudgetNodeSeed("department_budget", None, "Department Budget", "학과예산"),
    BudgetNodeSeed(
        "academic_development",
        "department_budget",
        "Academic Development Fund",
        "학사계발비",
    ),
    BudgetNodeSeed("supplies", "academic_development", "Supplies", "비품비", "supplies"),
]
