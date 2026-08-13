from app.config.budgets import BUDGET_SEEDS
from app.config.categories import CATEGORY_SEEDS
from app.config.departments import department_seeds
from app.config.workflows import workflow_rule_seeds
from app.domain.models import (
    ApprovalRule,
    ApprovalRuleStep,
    BudgetProgram,
    Department,
    EvidenceRequirementDefinition,
    ExpenseCategory,
)


def departments() -> list[Department]:
    return [
        Department(
            id=item.id,
            name_en=item.name_en,
            name_ko=item.name_ko,
            approval_channel_id=item.approval_channel_id,
        )
        for item in department_seeds()
    ]


def budgets() -> list[BudgetProgram]:
    return [
        BudgetProgram(
            id=item.id,
            name_en=item.name_en,
            name_ko=item.name_ko,
            is_available=item.is_available,
        )
        for item in BUDGET_SEEDS
    ]


def categories() -> list[ExpenseCategory]:
    result: list[ExpenseCategory] = []
    for category in CATEGORY_SEEDS:
        requirements = tuple(
            EvidenceRequirementDefinition(
                id=f"{category.id}.{item.key}",
                category_id=category.id,
                evidence_key=item.key,
                name_en=item.name_en,
                name_ko=item.name_ko,
                timing=item.timing,
                requirement=item.requirement,
                allow_waiver=item.allow_waiver,
                description_en=item.description_en,
                description_ko=item.description_ko,
                display_order=index,
            )
            for index, item in enumerate(category.evidence, start=1)
        )
        result.append(
            ExpenseCategory(
                id=category.id,
                budget_program_id=category.budget_program_id,
                name_en=category.name_en,
                name_ko=category.name_ko,
                evidence_requirements=requirements,
            )
        )
    return result


def department_by_id(department_id: str) -> Department | None:
    return next((item for item in departments() if item.id == department_id), None)


def budget_by_id(budget_id: str) -> BudgetProgram | None:
    return next((item for item in budgets() if item.id == budget_id), None)


def category_by_id(category_id: str) -> ExpenseCategory | None:
    return next((item for item in categories() if item.id == category_id), None)


def default_rule(department_id: str, category_id: str) -> ApprovalRule | None:
    seed = next(
        (
            item
            for item in workflow_rule_seeds()
            if item.department_id == department_id and item.category_id == category_id
        ),
        None,
    )
    if seed is None:
        return None
    return ApprovalRule(
        department_id=seed.department_id,
        budget_program_id=seed.budget_program_id,
        category_id=seed.category_id,
        approval_channel_id=None,
        steps=tuple(
            ApprovalRuleStep(
                name_en=step.name_en,
                name_ko=step.name_ko,
                approver_slack_user_ids=(),
            )
            for step in seed.steps
        ),
    )
