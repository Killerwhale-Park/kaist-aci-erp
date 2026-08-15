import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult

from app.config.settings import Settings
from app.database import Database
from app.slack.app import create_slack_app


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
async def test_signed_http_response_is_not_returned_before_listener_finishes() -> None:
    signing_secret = "test-secret"
    listener_started = asyncio.Event()
    allow_listener_to_finish = asyncio.Event()
    listener_finished = asyncio.Event()

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
        listener_started.set()
        await allow_listener_to_finish.wait()
        listener_finished.set()

    handler = AsyncSlackRequestHandler(slack_app)
    http_app = FastAPI()

    @http_app.post("/slack/events")
    async def slack_events(request: Request):
        return await handler.handle(request)

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
    signature = "v0=" + hmac.new(
        signing_secret.encode(),
        f"v0:{timestamp}:{raw_body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": signature,
    }

    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_task = asyncio.create_task(
            client.post("/slack/events", content=raw_body, headers=headers)
        )
        await asyncio.wait_for(listener_started.wait(), timeout=1)
        await asyncio.sleep(0)

        assert request_task.done() is False

        allow_listener_to_finish.set()
        response = await asyncio.wait_for(request_task, timeout=1)

    assert response.status_code == 200
    assert listener_finished.is_set()
