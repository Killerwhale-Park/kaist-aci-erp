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
    database_url: str = "sqlite:///./expense_erp.db"
    auto_create_schema: bool = True
    seed_configuration: bool = True

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    system_admin_slack_user_ids: str = ""

    department_1_approval_channel_id: str = "C_REPLACE_DEPARTMENT_1"
    department_2_approval_channel_id: str = "C_REPLACE_DEPARTMENT_2"
    department_3_approval_channel_id: str = "C_REPLACE_DEPARTMENT_3"
    department_4_approval_channel_id: str = "C_REPLACE_DEPARTMENT_4"

    department_1_professor_slack_user_id: str = "U_REPLACE_D1_PROFESSOR"
    department_1_administration_slack_user_id: str = "U_REPLACE_D1_ADMIN"
    department_1_inspector_slack_user_id: str = "U_REPLACE_D1_INSPECTOR"
    department_2_professor_slack_user_id: str = "U_REPLACE_D2_PROFESSOR"
    department_2_administration_slack_user_id: str = "U_REPLACE_D2_ADMIN"
    department_2_inspector_slack_user_id: str = "U_REPLACE_D2_INSPECTOR"
    department_3_professor_slack_user_id: str = "U_REPLACE_D3_PROFESSOR"
    department_3_administration_slack_user_id: str = "U_REPLACE_D3_ADMIN"
    department_3_inspector_slack_user_id: str = "U_REPLACE_D3_INSPECTOR"
    department_4_professor_slack_user_id: str = "U_REPLACE_D4_PROFESSOR"
    department_4_administration_slack_user_id: str = "U_REPLACE_D4_ADMIN"
    department_4_inspector_slack_user_id: str = "U_REPLACE_D4_INSPECTOR"

    @property
    def system_admin_ids(self) -> set[str]:
        return {
            value.strip() for value in self.system_admin_slack_user_ids.split(",") if value.strip()
        }

    def approval_channel_for(self, department_id: str) -> str:
        return getattr(self, f"{department_id}_approval_channel_id")

    def approver_for(self, department_id: str, reviewer: str) -> str:
        return getattr(self, f"{department_id}_{reviewer}_slack_user_id")


@lru_cache
def get_settings() -> Settings:
    return Settings()
