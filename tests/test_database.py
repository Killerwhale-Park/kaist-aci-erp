import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.database import normalize_database_url
from app.ledger.schema import REQUIRED_DATABASE_REVISION


def test_required_database_revision_matches_alembic_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert REQUIRED_DATABASE_REVISION == scripts.get_current_head()


def test_database_urls_use_async_drivers() -> None:
    assert normalize_database_url("postgres://user:pass@example/db") == (
        "postgresql+psycopg://user:pass@example/db"
    )
    assert normalize_database_url("postgresql://user:pass@example/db") == (
        "postgresql+psycopg://user:pass@example/db"
    )
    assert normalize_database_url("sqlite:///local.db") == "sqlite+aiosqlite:///local.db"


def test_preconfigured_async_database_url_is_unchanged() -> None:
    url = "sqlite+aiosqlite:///:memory:"
    assert normalize_database_url(url) == url


@pytest.mark.asyncio
async def test_sqlite_enforces_foreign_keys(database) -> None:
    async with database.session() as session:
        assert await session.scalar(text("PRAGMA foreign_keys")) == 1
