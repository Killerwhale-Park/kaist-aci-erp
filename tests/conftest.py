from collections import defaultdict
from typing import Any

import pytest

from app.config.settings import Settings


class FakeSlackClient:
    def __init__(self) -> None:
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.counter = 0

    def _next_ts(self) -> str:
        self.counter += 1
        return f"{self.counter:010d}.000000"

    async def chat_postMessage(self, **kwargs):
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
        roots = [
            message for message in self.messages[kwargs["channel"]] if not message.get("thread_ts")
        ]
        return {"ok": True, "messages": sorted(roots, key=lambda item: item["ts"], reverse=True)}

    async def conversations_replies(self, **kwargs):
        channel_messages = self.messages[kwargs["channel"]]
        selected = [
            message
            for message in channel_messages
            if message["ts"] == kwargs["ts"] or message.get("thread_ts") == kwargs["ts"]
        ]
        return {"ok": True, "messages": sorted(selected, key=lambda item: item["ts"])}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        slack_ledger_channel_id="C_LEDGER",
        bootstrap_system_admin_slack_user_ids="U_ADMIN",
    )


@pytest.fixture
def slack_client() -> FakeSlackClient:
    return FakeSlackClient()
