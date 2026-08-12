from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetSeed:
    id: str
    name_en: str
    name_ko: str
    is_available: bool


BUDGET_SEEDS = [
    BudgetSeed("student_support", "Student Support Budget", "학생 지원 예산", True),
    BudgetSeed(
        "ai_global_explorer", "AI Global Explorer Program", "AI 글로벌 탐방 프로그램", False
    ),
    BudgetSeed("resource_support", "Resource Support", "리소스 지원", False),
]
