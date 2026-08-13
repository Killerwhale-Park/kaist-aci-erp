from dataclasses import dataclass

LEGACY_BUDGET_IDS = {"student_support": "department_budget"}


@dataclass(frozen=True)
class BudgetNodeSeed:
    id: str
    parent_id: str | None
    name_en: str
    name_ko: str


BUDGET_NODE_SEEDS = [
    BudgetNodeSeed("department_budget", None, "Department Budget", "학과예산"),
    BudgetNodeSeed(
        "academic_development",
        "department_budget",
        "Academic Development Fund",
        "학사계발비",
    ),
    BudgetNodeSeed("supplies", "academic_development", "Supplies", "비품비"),
]
