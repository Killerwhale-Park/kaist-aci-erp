from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetWorkflowMappingSeed:
    budget_node_id: str
    workflow_id: str
    department_id: str | None = None


BUDGET_WORKFLOW_MAPPING_SEEDS = [
    BudgetWorkflowMappingSeed("academic_development", "academic_development_approval"),
]
