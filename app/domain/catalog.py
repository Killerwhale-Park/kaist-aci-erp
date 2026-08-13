from app.config.budgets import BUDGET_NODE_SEEDS
from app.config.departments import department_seeds
from app.config.form_mappings import BUDGET_FORM_MAPPING_SEEDS
from app.config.forms import EXPENSE_FORM_SEEDS
from app.config.workflow_mappings import BUDGET_WORKFLOW_MAPPING_SEEDS
from app.config.workflows import APPROVAL_WORKFLOW_SEEDS
from app.domain.enums import BudgetFormScope
from app.domain.models import (
    ApprovalWorkflowDefinition,
    ApprovalWorkflowStepDefinition,
    BudgetFormMapping,
    BudgetNode,
    BudgetProgram,
    BudgetWorkflowMapping,
    Department,
    EvidenceRequirementDefinition,
    ExpenseCategory,
    ExpenseForm,
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
            is_available=True,
            form_scope=item.form_scope,
        )
        for item in budget_nodes()
        if item.parent_id is None
    ]


def budget_nodes() -> list[BudgetNode]:
    return [
        BudgetNode(
            id=item.id,
            parent_id=item.parent_id,
            name_en=item.name_en,
            name_ko=item.name_ko,
            form_scope=item.form_scope,
        )
        for item in BUDGET_NODE_SEEDS
    ]


def budget_node_by_id(node_id: str) -> BudgetNode | None:
    return next((item for item in budget_nodes() if item.id == node_id), None)


def budget_children(parent_id: str | None) -> list[BudgetNode]:
    return [item for item in budget_nodes() if item.parent_id == parent_id]


def budget_path_from_nodes(nodes: list[BudgetNode], node_id: str) -> tuple[BudgetNode, ...]:
    nodes_by_id = {item.id: item for item in nodes}
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


def budget_path(node_id: str) -> tuple[BudgetNode, ...]:
    return budget_path_from_nodes(budget_nodes(), node_id)


def expense_forms() -> list[ExpenseForm]:
    result: list[ExpenseForm] = []
    for form in EXPENSE_FORM_SEEDS:
        requirements = tuple(
            EvidenceRequirementDefinition(
                id=f"{form.id}.{item.key}",
                category_id=form.id,
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
            for index, item in enumerate(form.evidence, start=1)
        )
        result.append(
            ExpenseForm(
                id=form.id,
                name_en=form.name_en,
                name_ko=form.name_ko,
                evidence_requirements=requirements,
            )
        )
    return result


def budget_form_mappings() -> list[BudgetFormMapping]:
    return [
        BudgetFormMapping(
            budget_node_id=item.budget_node_id,
            form_id=item.form_id,
            department_id=item.department_id,
        )
        for item in BUDGET_FORM_MAPPING_SEEDS
    ]


def resolve_expense_categories(
    nodes: list[BudgetNode],
    forms: list[ExpenseForm],
    mappings: list[BudgetFormMapping],
    department_id: str | None = None,
) -> list[ExpenseCategory]:
    node_by_id = {item.id: item for item in nodes}
    form_by_id = {item.id: item for item in forms}
    configured_keys = {(item.budget_node_id, item.department_id) for item in mappings}
    if len(configured_keys) != len(mappings):
        raise ValueError("A budget node can map to only one form per department")

    selected_mappings: dict[str, BudgetFormMapping] = {}
    for mapping in mappings:
        if mapping.department_id is None:
            selected_mappings[mapping.budget_node_id] = mapping
    if department_id is not None:
        for mapping in mappings:
            if mapping.department_id == department_id:
                selected_mappings[mapping.budget_node_id] = mapping

    result: list[ExpenseCategory] = []
    for mapping in selected_mappings.values():
        node = node_by_id.get(mapping.budget_node_id)
        form = form_by_id.get(mapping.form_id)
        if node is None or form is None:
            raise ValueError("Budget form mapping references unavailable configuration")
        if any(item.parent_id == node.id for item in nodes):
            raise ValueError("Only a leaf budget node can map to an expense form")
        path = budget_path_from_nodes(nodes, node.id)
        root_scope = path[0].form_scope or BudgetFormScope.GLOBAL
        if mapping.department_id is not None and root_scope != BudgetFormScope.DEPARTMENT:
            raise ValueError("Global budget programs cannot have department-specific forms")
        result.append(
            ExpenseCategory(
                id=node.id,
                budget_program_id=path[0].id,
                form_id=form.id,
                form_name_en=form.name_en,
                form_name_ko=form.name_ko,
                name_en=node.name_en,
                name_ko=node.name_ko,
                evidence_requirements=tuple(
                    EvidenceRequirementDefinition(
                        id=item.id,
                        category_id=node.id,
                        evidence_key=item.evidence_key,
                        name_en=item.name_en,
                        name_ko=item.name_ko,
                        timing=item.timing,
                        requirement=item.requirement,
                        display_order=item.display_order,
                        allow_waiver=item.allow_waiver,
                        description_en=item.description_en,
                        description_ko=item.description_ko,
                        is_active=item.is_active,
                    )
                    for item in form.evidence_requirements
                ),
                budget_path_en=tuple(item.name_en for item in path),
                budget_path_ko=tuple(item.name_ko for item in path),
            )
        )
    return result


def categories(department_id: str | None = None) -> list[ExpenseCategory]:
    return resolve_expense_categories(
        budget_nodes(), expense_forms(), budget_form_mappings(), department_id
    )


def department_by_id(department_id: str) -> Department | None:
    return next((item for item in departments() if item.id == department_id), None)


def budget_by_id(budget_id: str) -> BudgetProgram | None:
    return next((item for item in budgets() if item.id == budget_id), None)


def category_by_id(category_id: str, department_id: str | None = None) -> ExpenseCategory | None:
    return next((item for item in categories(department_id) if item.id == category_id), None)


def category_for_budget_node(
    node_id: str, department_id: str | None = None
) -> ExpenseCategory | None:
    return category_by_id(node_id, department_id)


def approval_workflows() -> list[ApprovalWorkflowDefinition]:
    return [
        ApprovalWorkflowDefinition(
            id=workflow.id,
            name_en=workflow.name_en,
            name_ko=workflow.name_ko,
            steps=tuple(
                ApprovalWorkflowStepDefinition(
                    id=step.id,
                    name_en=step.name_en,
                    name_ko=step.name_ko,
                    approver_roles=step.approver_roles,
                )
                for step in workflow.steps
            ),
        )
        for workflow in APPROVAL_WORKFLOW_SEEDS
    ]


def budget_workflow_mappings() -> list[BudgetWorkflowMapping]:
    return [
        BudgetWorkflowMapping(
            budget_node_id=mapping.budget_node_id,
            workflow_id=mapping.workflow_id,
            department_id=mapping.department_id,
        )
        for mapping in BUDGET_WORKFLOW_MAPPING_SEEDS
    ]


def workflow_for_budget_node(
    node_id: str, department_id: str | None = None
) -> ApprovalWorkflowDefinition | None:
    path = budget_path(node_id)
    workflow_by_id = {item.id: item for item in approval_workflows()}
    mapping_by_key = {
        (item.budget_node_id, item.department_id): item for item in budget_workflow_mappings()
    }
    selected: BudgetWorkflowMapping | None = None
    for node in path:
        selected = mapping_by_key.get((node.id, department_id)) or mapping_by_key.get(
            (node.id, None), selected
        )
    if selected is None:
        return None
    return workflow_by_id.get(selected.workflow_id)
