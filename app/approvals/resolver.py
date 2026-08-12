from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    ApprovalRule,
    ApprovalWorkflowDefinition,
    EvidenceRequirementDefinition,
)
from app.exceptions import ConfigurationError


class ApprovalRuleResolver:
    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_workflow(
        self, department_id: str, budget_program_id: str, category_id: str
    ) -> ApprovalWorkflowDefinition:
        statement = (
            select(ApprovalRule)
            .where(
                ApprovalRule.department_id == department_id,
                ApprovalRule.budget_program_id == budget_program_id,
                ApprovalRule.category_id == category_id,
                ApprovalRule.is_active.is_(True),
            )
            .options(
                selectinload(ApprovalRule.workflow).selectinload(ApprovalWorkflowDefinition.steps)
            )
        )
        rule = self.session.scalar(statement)
        if rule is None or not rule.workflow.is_active:
            raise ConfigurationError("No active approval workflow matches this request")
        if not rule.workflow.steps:
            raise ConfigurationError("The selected approval workflow has no steps")
        return rule.workflow

    def evidence_requirements(self, category_id: str) -> list[EvidenceRequirementDefinition]:
        statement = (
            select(EvidenceRequirementDefinition)
            .where(
                EvidenceRequirementDefinition.category_id == category_id,
                EvidenceRequirementDefinition.is_active.is_(True),
            )
            .order_by(EvidenceRequirementDefinition.display_order)
        )
        return list(self.session.scalars(statement))
