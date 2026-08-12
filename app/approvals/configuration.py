import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import ApprovalPolicy, UserRole
from app.db.models import (
    ApprovalRule,
    ApprovalStepDefinition,
    ApprovalStepDefinitionApprover,
    ApprovalWorkflowDefinition,
    Department,
    ExpenseCategory,
    UserProfile,
)
from app.exceptions import ApprovalPermissionError, ConfigurationError, EntityNotFoundError


@dataclass(frozen=True)
class ApprovalStepConfiguration:
    name_en: str
    name_ko: str
    approver_slack_user_ids: tuple[str, ...]


class ApprovalConfigurationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def assert_system_admin(self, slack_user_id: str) -> UserProfile:
        profile = self.session.get(UserProfile, slack_user_id)
        if profile is None or profile.role != UserRole.SYSTEM_ADMIN:
            raise ApprovalPermissionError("System administrator permission is required")
        return profile

    def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        statement = (
            select(ApprovalRule)
            .where(
                ApprovalRule.department_id == department_id,
                ApprovalRule.category_id == category_id,
            )
            .options(
                selectinload(ApprovalRule.workflow)
                .selectinload(ApprovalWorkflowDefinition.steps)
                .selectinload(ApprovalStepDefinition.approvers)
            )
        )
        rule = self.session.scalar(statement)
        if rule is None:
            raise EntityNotFoundError("Approval rule not found")
        return rule

    def save_rule(
        self,
        actor_slack_user_id: str,
        department_id: str,
        category_id: str,
        approval_channel_id: str,
        steps: list[ApprovalStepConfiguration],
    ) -> ApprovalRule:
        self.assert_system_admin(actor_slack_user_id)
        if not steps:
            raise ConfigurationError("An approval workflow must have at least one step")

        department = self.session.get(Department, department_id)
        category = self.session.get(ExpenseCategory, category_id)
        if department is None or not department.is_active:
            raise ConfigurationError("Department not found")
        if category is None or not category.is_active:
            raise ConfigurationError("Expense category not found")

        for step in steps:
            if not step.name_en.strip() or not step.name_ko.strip():
                raise ConfigurationError("Every approval step must have bilingual names")

        existing_rule = self.get_rule(department_id, category_id)
        next_version = existing_rule.workflow.version + 1
        workflow_id = f"wf_{department_id}_{category_id}_v{next_version}_{uuid.uuid4().hex[:8]}"
        workflow = ApprovalWorkflowDefinition(
            id=workflow_id,
            name_en=f"{department.name_en} {category.name_en} approval workflow",
            name_ko=f"{department.name_ko} {category.name_ko} 승인 절차",
            version=next_version,
        )
        for step_order, configuration in enumerate(steps, start=1):
            definition = ApprovalStepDefinition(
                id=f"{workflow_id}.step_{step_order}",
                step_order=step_order,
                name_en=configuration.name_en.strip(),
                name_ko=configuration.name_ko.strip(),
                approval_policy=ApprovalPolicy.ANY,
                required=True,
            )
            definition.approvers.extend(
                ApprovalStepDefinitionApprover(slack_user_id=slack_user_id)
                for slack_user_id in sorted(set(configuration.approver_slack_user_ids))
            )
            workflow.steps.append(definition)

        department.approval_channel_id = approval_channel_id
        existing_rule.workflow = workflow
        existing_rule.is_active = True
        self.session.add(workflow)
        self.session.flush()
        return existing_rule

    def system_admin_ids(self) -> list[str]:
        statement = (
            select(UserProfile.slack_user_id)
            .where(UserProfile.role == UserRole.SYSTEM_ADMIN)
            .order_by(UserProfile.slack_user_id)
        )
        return list(self.session.scalars(statement))

    def replace_system_admins(self, actor_slack_user_id: str, slack_user_ids: list[str]) -> None:
        self.assert_system_admin(actor_slack_user_id)
        selected_ids = set(slack_user_ids)
        if not selected_ids:
            raise ConfigurationError("At least one system administrator is required")

        current_admins = list(
            self.session.scalars(
                select(UserProfile).where(UserProfile.role == UserRole.SYSTEM_ADMIN)
            )
        )
        for profile in current_admins:
            if profile.slack_user_id not in selected_ids:
                profile.role = UserRole.REQUESTER
        for slack_user_id in selected_ids:
            profile = self.session.get(UserProfile, slack_user_id)
            if profile is None:
                profile = UserProfile(
                    slack_user_id=slack_user_id,
                    display_name=slack_user_id,
                    role=UserRole.SYSTEM_ADMIN,
                )
                self.session.add(profile)
            else:
                profile.role = UserRole.SYSTEM_ADMIN
