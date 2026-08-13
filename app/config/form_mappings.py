from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetFormMappingSeed:
    budget_node_id: str
    form_id: str
    department_id: str | None = None


BUDGET_FORM_MAPPING_SEEDS = [
    BudgetFormMappingSeed("supplies", "supplies_settlement"),
]
