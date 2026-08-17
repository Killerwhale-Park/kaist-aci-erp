from dataclasses import dataclass

from app.domain.catalog import category_for_budget_node, department_by_id
from app.exceptions import ConfigurationError


@dataclass(frozen=True)
class ConfigureRequestContextCommand:
    actor_slack_user_id: str
    conversation_id: str
    department_id: str
    budget_node_id: str


class RequestContextService:
    """Manage reusable input defaults without coupling them to approval routing."""

    def __init__(self, repository) -> None:
        self.repository = repository

    async def get(self, conversation_id: str):
        return await self.repository.request_context(conversation_id)

    async def configure(self, command: ConfigureRequestContextCommand):
        department = department_by_id(command.department_id)
        category = category_for_budget_node(command.budget_node_id, command.department_id)
        if department is None or category is None:
            raise ConfigurationError("Request context references unavailable configuration")
        return await self.repository.save_request_context(
            command.actor_slack_user_id,
            conversation_id=command.conversation_id,
            department_id=command.department_id,
            budget_node_id=command.budget_node_id,
        )
