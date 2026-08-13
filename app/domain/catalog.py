from app.config.budgets import BUDGET_NODE_SEEDS, BUDGET_SEEDS
from app.config.categories import CATEGORY_SEEDS
from app.config.departments import department_seeds
from app.config.workflows import workflow_rule_seeds
from app.domain.models import (
    ApprovalRule,
    ApprovalRuleStep,
    BudgetNode,
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


def budget_nodes() -> list[BudgetNode]:
    return [
        BudgetNode(
            id=item.id,
            parent_id=item.parent_id,
            name_en=item.name_en,
            name_ko=item.name_ko,
            expense_category_id=item.expense_category_id,
        )
        for item in BUDGET_NODE_SEEDS
    ]


def budget_node_by_id(node_id: str) -> BudgetNode | None:
    return next((item for item in budget_nodes() if item.id == node_id), None)


def budget_children(parent_id: str | None) -> list[BudgetNode]:
    return [item for item in budget_nodes() if item.parent_id == parent_id]


def budget_path(node_id: str) -> tuple[BudgetNode, ...]:
    nodes_by_id = {item.id: item for item in budget_nodes()}
    path: list[BudgetNode] = []
    seen: set[str] = set()
    current = nodes_by_id.get(node_id)
    while current is not None:
        if current.id in seen:
            raise ValueError("Budget configuration contains a cycle")
        seen.add(current.id)
        path.append(current)
        current = nodes_by_id.get(current.parent_id) if current.parent_id else None
    path.reverse()
    return tuple(path)


def categories() -> list[ExpenseCategory]:
    result: list[ExpenseCategory] = []
    for category in CATEGORY_SEEDS:
        leaf = next(
            (item for item in budget_nodes() if item.expense_category_id == category.id),
            None,
        )
        path = budget_path(leaf.id) if leaf else ()
        if not path:
            raise ValueError(f"Expense category has no budget path: {category.id}")
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
                budget_program_id=path[0].id,
                name_en=category.name_en,
                name_ko=category.name_ko,
                evidence_requirements=requirements,
                budget_path_en=tuple(item.name_en for item in path),
                budget_path_ko=tuple(item.name_ko for item in path),
            )
        )
    return result


def department_by_id(department_id: str) -> Department | None:
    return next((item for item in departments() if item.id == department_id), None)


def budget_by_id(budget_id: str) -> BudgetProgram | None:
    return next((item for item in budgets() if item.id == budget_id), None)


def category_by_id(category_id: str) -> ExpenseCategory | None:
    return next((item for item in categories() if item.id == category_id), None)


def category_for_budget_node(node_id: str) -> ExpenseCategory | None:
    node = budget_node_by_id(node_id)
    if node is None or node.expense_category_id is None:
        return None
    return category_by_id(node.expense_category_id)


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
