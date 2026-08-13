from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.config.settings import get_settings
from app.slack.app import create_slack_app

settings = get_settings()
slack_app = create_slack_app(settings)
slack_handler = AsyncSlackRequestHandler(slack_app)


app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    if not (
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_ledger_channel_id
    ):
        return {"status": "configuration_required"}
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)
