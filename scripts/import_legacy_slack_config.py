"""One-time import of the former Slack system snapshot into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.config.roles import WORKSPACE_ROLE_SCOPE, empty_role_set, role_ids
from app.config.settings import Settings
from app.database import Database
from app.exceptions import ConfigurationError
from app.ledger.codec import decode_chunks
from app.ledger.repository import LedgerRepository

SYSTEM_CONFIG_ROOT = "system_configuration_snapshot"
SYSTEM_CONFIG_CHUNK = "system_configuration_chunk"
SYSTEM_CONFIG_SNAPSHOT = "SYSTEM_CONFIGURATION_SNAPSHOT"


def metadata_type(message: dict[str, Any]) -> str | None:
    return (message.get("metadata") or {}).get("event_type")


async def find_snapshot(client: AsyncWebClient, channel_id: str) -> dict[str, Any]:
    cursor: str | None = None
    for _ in range(10):
        response = await client.conversations_history(
            channel=channel_id,
            limit=100,
            include_all_metadata=True,
            **({"cursor": cursor} if cursor else {}),
        )
        root = next(
            (
                message
                for message in response.get("messages", [])
                if metadata_type(message) == SYSTEM_CONFIG_ROOT
            ),
            None,
        )
        if root is not None:
            payload = (root.get("metadata") or {}).get("event_payload") or {}
            messages = []
            if payload.get("inline_record"):
                messages.append(
                    {
                        "ts": root["ts"],
                        "metadata": {
                            "event_type": SYSTEM_CONFIG_CHUNK,
                            "event_payload": payload["inline_record"],
                        },
                    }
                )
            else:
                replies = await client.conversations_replies(
                    channel=channel_id,
                    ts=root["ts"],
                    limit=999,
                    include_all_metadata=True,
                )
                messages.extend(replies.get("messages", []))
            records = decode_chunks(messages, event_type=SYSTEM_CONFIG_CHUNK)
            snapshot = next(
                (
                    record["data"]
                    for record in records
                    if record["record_type"] == SYSTEM_CONFIG_SNAPSHOT
                ),
                None,
            )
            if snapshot is None:
                raise ConfigurationError("The Slack system snapshot is incomplete")
            return snapshot
        cursor = response.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    raise ConfigurationError("No Slack system snapshot was found")


async def import_snapshot(channel_id: str, actor: str) -> None:
    settings = Settings()
    if not settings.slack_bot_token or not settings.database_url:
        raise ConfigurationError("SLACK_BOT_TOKEN and DATABASE_URL are required")
    client = AsyncWebClient(token=settings.slack_bot_token)
    database = Database(settings.database_url)
    repository = LedgerRepository(client, database)
    try:
        snapshot = await find_snapshot(client, channel_id)
        workspace = empty_role_set()
        for role_id in role_ids():
            workspace[role_id].update(snapshot.get("roles", {}).get(role_id, []))
        await repository.replace_role_assignments(
            actor,
            {WORKSPACE_ROLE_SCOPE: workspace},
        )

        channels = snapshot.get("system_channels", {})
        audit_channel_id = channels.get("audit_channel_id")
        alerts_channel_id = channels.get("alerts_channel_id")
        if audit_channel_id and alerts_channel_id:
            await repository.replace_system_channels(
                actor,
                audit_channel_id=audit_channel_id,
                alerts_channel_id=alerts_channel_id,
                additional_operating_channel_ids=channels.get(
                    "additional_operating_channel_ids", []
                ),
            )

        routes = snapshot.get("approval_routes", {})
        for route in routes.values():
            if route.get("approval_channel_id"):
                await repository.save_approval_route(
                    actor,
                    route["department_id"],
                    route["category_id"],
                    route["approval_channel_id"],
                )
        role_count = sum(map(len, workspace.values()))
        print(f"Imported {role_count} role assignments and {len(routes)} routes")
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="Former Slack System Config channel ID")
    parser.add_argument("--actor", required=True, help="Bootstrap system administrator Slack ID")
    arguments = parser.parse_args()
    asyncio.run(import_snapshot(arguments.channel, arguments.actor))


if __name__ == "__main__":
    main()
