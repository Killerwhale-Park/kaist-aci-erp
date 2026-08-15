from fastapi import Request
from starlette.background import BackgroundTasks

from app.slack.deferred import capture_deferred_work, run_deferred_work


async def handle_slack_request(slack_handler, request: Request):
    """Return Slack's acknowledgement before running tracked follow-up work."""

    with capture_deferred_work() as deferred:
        response = await slack_handler.handle(request)
    if not deferred:
        return response

    tasks = BackgroundTasks()
    if response.background is not None:
        tasks.add_task(response.background)
    tasks.add_task(run_deferred_work, deferred)
    response.background = tasks
    return response
