import asyncio
import logging

from slack_sdk.errors import SlackApiError

from app.application.dashboard import load_user_dashboard
from app.database import Database
from app.ledger import LedgerRepository
from app.slack.home import app_home_view
from app.slack.surfaces import SlackSurfaces

logger = logging.getLogger(__name__)


class SlackRuntime:
    """Infrastructure services shared by thin Slack event controllers."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def repository(self, client) -> LedgerRepository:
        return LedgerRepository(client, self.database)

    def surfaces(self, client) -> SlackSurfaces:
        ledger = self.repository(client)
        return SlackSurfaces(client, ledger.report_alert)

    async def publish_home(self, client, slack_user_id: str) -> None:
        dashboard = await load_user_dashboard(self.repository(client), slack_user_id)
        await client.views_publish(user_id=slack_user_id, view=app_home_view(dashboard))

    async def publish_homes(self, client, *slack_user_ids: str) -> None:
        user_ids = sorted({user_id for user_id in slack_user_ids if user_id})
        results = await asyncio.gather(
            *(self.publish_home(client, user_id) for user_id in user_ids),
            return_exceptions=True,
        )
        for user_id, result in zip(user_ids, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to publish App Home for %s: %s",
                    user_id,
                    type(result).__name__,
                )

    async def safe_dm(
        self,
        client,
        slack_user_id: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> None:
        try:
            await client.chat_postMessage(channel=slack_user_id, text=text, blocks=blocks)
        except SlackApiError:
            logger.exception("Failed to send Slack DM to %s", slack_user_id)

    async def safe_alert(self, client, text: str) -> None:
        try:
            await self.repository(client).report_alert(text)
        except Exception:
            logger.exception("Failed to publish a system alert")
