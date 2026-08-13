from app.database import normalize_database_url


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
