import asyncio

import pytest
from starlette.background import BackgroundTask
from starlette.responses import Response

from app.slack.deferred import capture_deferred_work, defer, run_deferred_work


@pytest.mark.asyncio
async def test_deferred_work_starts_after_response_body_is_sent() -> None:
    background_started = asyncio.Event()
    allow_background_to_finish = asyncio.Event()
    response_sent = asyncio.Event()

    async def background() -> None:
        background_started.set()
        await allow_background_to_finish.wait()

    with capture_deferred_work() as factories:
        await defer(background)

    response = Response(status_code=200)
    response.background = BackgroundTask(run_deferred_work, factories)
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_sent.set()

    response_task = asyncio.create_task(
        response(
            {
                "type": "http",
                "method": "POST",
                "path": "/slack/events",
                "headers": [],
            },
            receive,
            send,
        )
    )
    await asyncio.wait_for(response_sent.wait(), timeout=1)
    await asyncio.wait_for(background_started.wait(), timeout=1)

    assert response_task.done() is False
    assert [message["type"] for message in messages] == [
        "http.response.start",
        "http.response.body",
    ]

    allow_background_to_finish.set()
    await asyncio.wait_for(response_task, timeout=1)


@pytest.mark.asyncio
async def test_defer_runs_inline_without_http_capture() -> None:
    completed = False

    async def work() -> None:
        nonlocal completed
        completed = True

    await defer(work)

    assert completed is True
