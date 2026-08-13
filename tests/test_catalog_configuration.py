from app.domain.catalog import budgets, departments


def test_operational_department_and_budget_names() -> None:
    assert [(item.id, item.name_en) for item in departments()] == [
        ("department_1", "AI Computing"),
        ("department_2", "AI System"),
        ("department_3", "AX"),
        ("department_4", "AI Future"),
    ]
    assert [(item.id, item.name_ko, item.name_en, item.is_available) for item in budgets()] == [
        ("department_budget", "학과예산", "Department Budget", True),
        ("academic_development", "학사계발비", "Academic Development Fund", False),
    ]
