from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_system_channel_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
