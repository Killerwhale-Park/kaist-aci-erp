from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetSeed:
    id: str
    name_en: str
    name_ko: str
    is_available: bool


BUDGET_SEEDS = [
    BudgetSeed("department_budget", "Department Budget", "학과예산", True),
    BudgetSeed("academic_development", "Academic Development Fund", "학사계발비", False),
]

LEGACY_BUDGET_IDS = {"student_support": "department_budget"}
