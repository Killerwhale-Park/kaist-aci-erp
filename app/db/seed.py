from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.budgets import BUDGET_SEEDS
from app.config.categories import CATEGORY_SEEDS
from app.config.departments import department_seeds
from app.config.settings import Settings, get_settings
from app.config.workflows import workflow_rule_seeds
from app.db.enums import UserRole
from app.db.models import (
    ApprovalRule,
    ApprovalStepDefinition,
    ApprovalWorkflowDefinition,
    BudgetProgram,
    Department,
    EvidenceRequirementDefinition,
    ExpenseCategory,
    UserProfile,
)
from app.db.session import session_scope


def seed_database(session: Session, settings: Settings) -> None:
    for item in department_seeds():
        if session.get(Department, item.id) is None:
            session.add(Department(**item.__dict__))

    for item in BUDGET_SEEDS:
        if session.get(BudgetProgram, item.id) is None:
            session.add(BudgetProgram(**item.__dict__))

    for category in CATEGORY_SEEDS:
        if session.get(ExpenseCategory, category.id) is None:
            session.add(
                ExpenseCategory(
                    id=category.id,
                    budget_program_id=category.budget_program_id,
                    name_en=category.name_en,
                    name_ko=category.name_ko,
                )
            )
        for display_order, evidence in enumerate(category.evidence, start=1):
            definition_id = f"{category.id}.{evidence.key}"
            if session.get(EvidenceRequirementDefinition, definition_id) is None:
                session.add(
                    EvidenceRequirementDefinition(
                        id=definition_id,
                        category_id=category.id,
                        evidence_key=evidence.key,
                        name_en=evidence.name_en,
                        name_ko=evidence.name_ko,
                        timing=evidence.timing,
                        requirement=evidence.requirement,
                        allow_waiver=evidence.allow_waiver,
                        description_en=evidence.description_en,
                        description_ko=evidence.description_ko,
                        display_order=display_order,
                    )
                )

    for item in workflow_rule_seeds():
        if session.get(ApprovalWorkflowDefinition, item.workflow_id) is None:
            session.add(
                ApprovalWorkflowDefinition(
                    id=item.workflow_id,
                    name_en=item.name_en,
                    name_ko=item.name_ko,
                    version=1,
                )
            )
        for step_order, step in enumerate(item.steps, start=1):
            definition_id = f"{item.workflow_id}.step_{step_order}"
            if session.get(ApprovalStepDefinition, definition_id) is None:
                session.add(
                    ApprovalStepDefinition(
                        id=definition_id,
                        workflow_definition_id=item.workflow_id,
                        step_order=step_order,
                        name_en=step.name_en,
                        name_ko=step.name_ko,
                        required=step.required,
                    )
                )
        if session.get(ApprovalRule, item.rule_id) is None:
            session.add(
                ApprovalRule(
                    id=item.rule_id,
                    department_id=item.department_id,
                    budget_program_id=item.budget_program_id,
                    category_id=item.category_id,
                    workflow_definition_id=item.workflow_id,
                )
            )

    admin_count = session.scalar(
        select(func.count())
        .select_from(UserProfile)
        .where(UserProfile.role == UserRole.SYSTEM_ADMIN)
    )
    if admin_count == 0:
        for slack_user_id in settings.bootstrap_system_admin_ids:
            profile = session.get(UserProfile, slack_user_id)
            if profile is None:
                session.add(
                    UserProfile(
                        slack_user_id=slack_user_id,
                        display_name=slack_user_id,
                        role=UserRole.SYSTEM_ADMIN,
                    )
                )
            else:
                profile.role = UserRole.SYSTEM_ADMIN


def main() -> None:
    settings = get_settings()
    with session_scope() as session:
        seed_database(session, settings)


if __name__ == "__main__":
    main()
