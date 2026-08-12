from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from sqlalchemy import text

from app.config.settings import get_settings
from app.db.seed import seed_database
from app.db.session import init_db, session_scope
from app.slack.app import create_slack_app

settings = get_settings()
slack_app = create_slack_app(settings)
slack_handler = AsyncSlackRequestHandler(slack_app)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_schema:
        init_db()
    if settings.seed_configuration:
        with session_scope() as session:
            seed_database(session, settings)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    with session_scope() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)
