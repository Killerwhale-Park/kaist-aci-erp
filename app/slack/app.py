from slack_bolt.async_app import AsyncApp

from app.config.settings import Settings
from app.database import Database
from app.slack.handlers import register_handlers


def create_slack_app(settings: Settings, database: Database) -> AsyncApp:
    slack_app = AsyncApp(
        token=settings.slack_bot_token or "xoxb-not-configured",
        signing_secret=settings.slack_signing_secret or "not-configured",
        # Vercel can stop the invocation as soon as the HTTP acknowledgement is
        # returned. Bolt's default mode runs the rest of a listener in a detached
        # task, which leaves an already-opened loading modal stranded forever.
        process_before_response=True,
    )
    register_handlers(slack_app, database)
    return slack_app
