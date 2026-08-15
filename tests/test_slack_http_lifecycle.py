import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult
from starlette.requests import Request

from app.config.settings import Settings
from app.database import Database
from app.slack.app import create_slack_app
from app.slack.deferred import defer
from app.slack.http import handle_slack_request


def test_production_slack_app_waits_for_listeners() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    settings = Settings(
        _env_file=None,
        slack_bot_token="xoxb-test",
        slack_signing_secret="test-secret",
        database_url=database.database_url,
    )

    slack_app = create_slack_app(settings, database)

    assert slack_app.process_before_response is True


@pytest.mark.asyncio
async def test_signed_http_response_sends_before_deferred_listener_work_finishes() -> None:
    signing_secret = "test-secret"
    background_started = asyncio.Event()
    allow_background_to_finish = asyncio.Event()
    background_finished = asyncio.Event()

    async def authorize(**_kwargs) -> AuthorizeResult:
        return AuthorizeResult(
            enterprise_id=None,
            team_id="T_TEST",
            bot_user_id="B_TEST",
            bot_token="xoxb-test",
        )

    slack_app = AsyncApp(
        signing_secret=signing_secret,
        authorize=authorize,
        process_before_response=True,
    )

    @slack_app.action("lifecycle_probe")
    async def lifecycle_probe(ack) -> None:
        await ack()

        async def background() -> None:
            background_started.set()
            await allow_background_to_finish.wait()
            background_finished.set()

        await defer(background)

    handler = AsyncSlackRequestHandler(slack_app)

    payload = {
        "type": "block_actions",
        "team": {"id": "T_TEST"},
        "user": {"id": "U_TEST"},
        "api_app_id": "A_TEST",
        "trigger_id": "test-trigger",
        "container": {"type": "view", "view_id": "V_TEST"},
        "actions": [
            {
                "type": "button",
                "action_id": "lifecycle_probe",
                "block_id": "probe",
                "action_ts": "1.000000",
                "value": "probe",
            }
        ],
    }
    raw_body = urlencode({"payload": json.dumps(payload, separators=(",", ":"))})
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            signing_secret.encode(),
            f"v0:{timestamp}:{raw_body}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": signature,
    }
    request_messages = [{"type": "http.request", "body": raw_body.encode(), "more_body": False}]

    async def receive() -> dict:
        if request_messages:
            return request_messages.pop(0)
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/slack/events",
        "raw_path": b"/slack/events",
        "query_string": b"",
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 443),
    }
    response = await handle_slack_request(
        handler,
        Request(scope, receive),
    )

    assert response.status_code == 200
    assert response.background is not None
    assert not background_started.is_set()

    sent: list[dict] = []
    response_body_sent = asyncio.Event()

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_body_sent.set()

    response_task = asyncio.create_task(response(scope, receive, send))
    await asyncio.wait_for(response_body_sent.wait(), timeout=1)
    await asyncio.wait_for(background_started.wait(), timeout=1)

    assert response_task.done() is False
    assert not background_finished.is_set()

    allow_background_to_finish.set()
    await asyncio.wait_for(response_task, timeout=1)
    assert background_finished.is_set()
