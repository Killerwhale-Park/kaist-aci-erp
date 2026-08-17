from app.config.roles import STUDENT_COORDINATOR_ROLE
from app.config.work_request_policies import work_request_policy
from app.domain.catalog import category_for_budget_node, department_by_id
from app.domain.enums import WorkRequestKind, WorkRequestStatus
from app.domain.work_requests import purchase_created_data, settlement_created_data
from app.exceptions import (
    ApprovalConfigurationError,
    ApprovalPermissionError,
    ConfigurationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand


class WorkRequestService:
    """Application boundary for creating purchase and settlement work."""

    def __init__(self, repository) -> None:
        self.repository = repository

    async def create_purchase(self, command: CreatePurchaseRequestCommand):
        department = department_by_id(command.department_id)
        if department is None:
            raise ConfigurationError("Unknown department")
        await self.repository.assert_operating_channel(command.channel_id)
        await self.repository.assert_can_submit_request(
            command.requester_slack_user_id,
            command.channel_id,
        )
        policy = work_request_policy(WorkRequestKind.PURCHASE)
        if policy.approval_workflow_id is None:
            raise ApprovalConfigurationError("Purchase workflow is not configured")
        try:
            workflow = await self.repository.resolve_approval_workflow(
                policy.approval_workflow_id,
                command.channel_id,
                actor_bindings={"payment_assignee": {command.assignee_slack_user_id}},
            )
        except EntityNotFoundError as error:
            raise ApprovalConfigurationError("Purchase workflow cannot be resolved") from error
        if not workflow.is_complete:
            raise ApprovalConfigurationError("Purchase workflow assignments are incomplete")
        return await self.repository.create_work_request(
            purchase_created_data(command, department, workflow)
        )

    async def create_settlement(self, command: CreateSettlementRequestCommand):
        department, category, delivery_channel_id = await self._settlement_configuration(command)
        await self.repository.assert_can_assign_settlement(command.requester_slack_user_id)
        await self._assert_settlement_assignee(command.assignee_slack_user_id)
        return await self.repository.create_work_request(
            settlement_created_data(
                command,
                department,
                category,
                delivery_channel_id,
            )
        )

    async def handoff_purchase(
        self,
        source_request_id: str,
        command: CreateSettlementRequestCommand,
    ):
        source = await self.repository.get_work_request(source_request_id)
        if source.kind != WorkRequestKind.PURCHASE:
            raise InvalidStateTransitionError("Only a purchase request can start this handoff")
        if source.status not in {WorkRequestStatus.ACTION_REQUIRED, WorkRequestStatus.OPEN}:
            raise InvalidStateTransitionError("Purchase request is not ready for settlement")
        if source.assignee_slack_user_id != command.requester_slack_user_id:
            raise ApprovalPermissionError("Only the purchase assignee can hand off settlement")
        if source.department_id != command.department_id:
            raise ConfigurationError("A purchase handoff cannot change departments")

        department, category, delivery_channel_id = await self._settlement_configuration(command)
        await self._assert_settlement_assignee(command.assignee_slack_user_id)
        created_data = settlement_created_data(
            command,
            department,
            category,
            delivery_channel_id,
            originator_slack_user_id=source.originator_slack_user_id,
            case_id=source.case_id,
            parent_request_id=source.id,
        )
        return await self.repository.handoff_work_request(
            source.id,
            command.requester_slack_user_id,
            created_data,
        )

    async def _settlement_configuration(self, command: CreateSettlementRequestCommand):
        department = department_by_id(command.department_id)
        if department is None:
            raise ConfigurationError("Unknown department")
        category = category_for_budget_node(command.budget_node_id, command.department_id)
        if category is None:
            raise ApprovalConfigurationError("Budget item has no expense form")
        try:
            rule = await self.repository.get_rule(command.department_id, category.id)
        except EntityNotFoundError as error:
            raise ApprovalConfigurationError(
                "Budget item approval procedure is unavailable"
            ) from error
        if not rule.is_complete or not rule.approval_channel_id:
            raise ApprovalConfigurationError("Budget item approval procedure is incomplete")
        return department, category, rule.approval_channel_id

    async def _assert_settlement_assignee(self, assignee_slack_user_id: str) -> None:
        coordinators = await self.repository.role_user_ids(STUDENT_COORDINATOR_ROLE)
        if assignee_slack_user_id not in coordinators:
            raise ApprovalPermissionError("Settlement assignee must be a student coordinator")
