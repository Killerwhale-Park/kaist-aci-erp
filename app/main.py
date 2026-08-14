from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from sqlalchemy import text

from app.config.settings import get_settings
from app.database import Database
from app.ledger.schema import REQUIRED_DATABASE_REVISION
from app.slack.app import create_slack_app

settings = get_settings()
database = Database(settings.database_url)
slack_app = create_slack_app(settings, database)
slack_handler = AsyncSlackRequestHandler(slack_app)


app = FastAPI(title="AIC Expense Approval")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    if not (settings.slack_bot_token and settings.slack_signing_secret and database.configured):
        return {"status": "configuration_required"}
    try:
        async with database.session() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception:
        return {"status": "database_unavailable"}
    if revision != REQUIRED_DATABASE_REVISION:
        return {"status": "migration_required"}
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)
