from dataclasses import dataclass

from app.domain.catalog import category_by_id, workflow_for_budget_node
from app.exceptions import ConfigurationError, EntityNotFoundError


@dataclass(frozen=True)
class ApprovalProcedureStep:
    name_en: str
    name_ko: str
    approver_roles: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalProcedureConfiguration:
    department_id: str
    category_id: str
    approval_channel_id: str | None
    workflow_name_en: str
    workflow_name_ko: str
    steps: tuple[ApprovalProcedureStep, ...]
    assigned_user_ids_by_role: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ConfigureApprovalProcedureCommand:
    actor_slack_user_id: str
    department_id: str
    category_id: str
    approval_channel_id: str
    assigned_user_ids_by_role: dict[str, tuple[str, ...]]


class ApprovalProcedureService:
    """Configure code-defined approval policy without coupling it to Slack views."""

    def __init__(self, repository) -> None:
        self.repository = repository

    @staticmethod
    def _definition(department_id: str, category_id: str):
        category = category_by_id(category_id, department_id)
        workflow = workflow_for_budget_node(category_id, department_id)
        if category is None or workflow is None:
            raise EntityNotFoundError("Approval workflow mapping not found")
        return workflow

    async def get(self, department_id: str, category_id: str) -> ApprovalProcedureConfiguration:
        workflow = self._definition(department_id, category_id)
        role_ids = {role_id for step in workflow.steps for role_id in step.approver_roles}
        channel_id, assignments = await self.repository.approval_procedure_configuration(
            department_id,
            category_id,
            role_ids,
        )
        return ApprovalProcedureConfiguration(
            department_id=department_id,
            category_id=category_id,
            approval_channel_id=channel_id,
            workflow_name_en=workflow.name_en,
            workflow_name_ko=workflow.name_ko,
            steps=tuple(
                ApprovalProcedureStep(
                    name_en=step.name_en,
                    name_ko=step.name_ko,
                    approver_roles=step.approver_roles,
                )
                for step in workflow.steps
            ),
            assigned_user_ids_by_role={
                role_id: tuple(sorted(assignments.get(role_id, set())))
                for role_id in sorted(role_ids)
            },
        )

    async def configure(self, command: ConfigureApprovalProcedureCommand) -> None:
        workflow = self._definition(command.department_id, command.category_id)
        required_roles = {role_id for step in workflow.steps for role_id in step.approver_roles}
        configured_roles = set(command.assigned_user_ids_by_role)
        if configured_roles != required_roles:
            raise ConfigurationError("Every workflow role must be configured exactly once")
        if any(not users for users in command.assigned_user_ids_by_role.values()):
            raise ConfigurationError("Every workflow role needs at least one assignee")

        await self.repository.assert_system_admin(command.actor_slack_user_id)
        channel_members = await self.repository.channel_member_ids(command.approval_channel_id)
        configured_users = {
            user_id for users in command.assigned_user_ids_by_role.values() for user_id in users
        }
        if not configured_users.issubset(channel_members):
            raise ConfigurationError(
                "Every configured approver must be a member of the approval channel"
            )

        await self.repository.configure_approval_procedure(
            command.actor_slack_user_id,
            command.department_id,
            command.category_id,
            command.approval_channel_id,
            {
                role_id: set(user_ids)
                for role_id, user_ids in command.assigned_user_ids_by_role.items()
            },
        )
