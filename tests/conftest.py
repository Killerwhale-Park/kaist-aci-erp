from collections import defaultdict
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

from app.config.settings import Settings
from app.database import Database
from app.ledger.tables import Base


class FakeSlackClient:
    def __init__(self) -> None:
        self.token = f"fake-{uuid4()}"
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.calls: dict[str, int] = defaultdict(int)
        self.private_channels = {
            "C_SYSTEM",
            "C_AUDIT",
            "C_ALERTS",
            "C_APPROVAL",
            "C_DEPARTMENT_2",
            "C_WORK",
        }
        self.channel_members = {
            "C_SYSTEM": {"U_ROOT"},
            "C_AUDIT": {"U_ROOT"},
            "C_ALERTS": {"U_ROOT"},
            "C_APPROVAL": {
                "U_REQUESTER",
                "U_COORDINATOR",
                "U_PROFESSOR",
                "U_ADMIN_STAFF",
            },
            "C_DEPARTMENT_2": {"U_OTHER_STUDENT", "U_OTHER_PROFESSOR"},
            "C_WORK": {"U_REQUESTER", "U_PROFESSOR"},
        }
        self.counter = 0

    def _next_ts(self) -> str:
        self.counter += 1
        return f"{self.counter:010d}.000000"

    async def chat_postMessage(self, **kwargs):
        self.calls["chat_postMessage"] += 1
        message = dict(kwargs)
        message["ts"] = self._next_ts()
        self.messages[kwargs["channel"]].append(message)
        return {"ok": True, "ts": message["ts"], "message": message}

    async def chat_update(self, **kwargs):
        for message in self.messages[kwargs["channel"]]:
            if message["ts"] == kwargs["ts"]:
                message.update({key: value for key, value in kwargs.items() if key != "channel"})
                return {"ok": True, "ts": message["ts"], "message": message}
        raise AssertionError("message not found")

    async def conversations_history(self, **kwargs):
        self.calls["conversations_history"] += 1
        roots = [
            message for message in self.messages[kwargs["channel"]] if not message.get("thread_ts")
        ]
        messages = sorted(roots, key=lambda item: item["ts"], reverse=True)
        if kwargs.get("oldest"):
            messages = [item for item in messages if item["ts"] >= kwargs["oldest"]]
        if kwargs.get("latest"):
            messages = [item for item in messages if item["ts"] <= kwargs["latest"]]
        return {"ok": True, "messages": messages[: kwargs.get("limit", 100)]}

    async def conversations_list(self, **kwargs):
        self.calls["conversations_list"] += 1
        return {
            "ok": True,
            "channels": [
                {"id": channel_id, "is_member": True, "is_private": True}
                for channel_id in sorted(self.private_channels)
            ],
        }

    async def conversations_info(self, **kwargs):
        self.calls["conversations_info"] += 1
        channel_id = kwargs["channel"]
        return {
            "ok": channel_id in self.private_channels,
            "channel": {
                "id": channel_id,
                "is_member": channel_id in self.private_channels,
                "is_private": True,
            },
        }

    async def conversations_members(self, **kwargs):
        self.calls["conversations_members"] += 1
        return {
            "ok": True,
            "members": sorted(self.channel_members.get(kwargs["channel"], set())),
        }

    async def conversations_replies(self, **kwargs):
        self.calls["conversations_replies"] += 1
        channel_messages = self.messages[kwargs["channel"]]
        selected = [
            message
            for message in channel_messages
            if message["ts"] == kwargs["ts"] or message.get("thread_ts") == kwargs["ts"]
        ]
        return {"ok": True, "messages": sorted(selected, key=lambda item: item["ts"])}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")


@pytest.fixture
def slack_client() -> FakeSlackClient:
    return FakeSlackClient()


@pytest_asyncio.fixture
async def database(tmp_path) -> Database:
    path = (tmp_path / "test.sqlite").as_posix()
    database = Database(f"sqlite+aiosqlite:///{path}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()
