from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Student Expense Support ERP"
    environment: str = "development"
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_ledger_channel_id: str = ""
    bootstrap_system_admin_slack_user_ids: str = ""

    @property
    def bootstrap_system_admin_ids(self) -> set[str]:
        return {
            value.strip()
            for value in self.bootstrap_system_admin_slack_user_ids.split(",")
            if value.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
