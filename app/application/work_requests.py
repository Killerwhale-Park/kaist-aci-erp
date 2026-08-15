from app.config.work_request_policies import work_request_policy
from app.domain.catalog import department_by_id
from app.domain.enums import WorkRequestKind
from app.domain.work_requests import purchase_created_data, settlement_created_data
from app.exceptions import ApprovalConfigurationError, ConfigurationError, EntityNotFoundError
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
        department = department_by_id(command.department_id)
        if department is None:
            raise ConfigurationError("Unknown department")
        await self.repository.assert_operating_channel(command.channel_id)
        await self.repository.assert_can_assign_settlement(
            command.requester_slack_user_id,
            command.channel_id,
        )
        return await self.repository.create_work_request(
            settlement_created_data(command, department)
        )
