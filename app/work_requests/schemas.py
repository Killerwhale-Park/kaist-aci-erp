from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


def _optional_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip() or None
    return value


def _https_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("A valid HTTPS URL is required")
    return value


class CreatePurchaseRequestCommand(BaseModel):
    requester_slack_user_id: str
    department_id: str
    assignee_slack_user_id: str
    channel_id: str
    source_conversation_id: str | None = None
    item_name: str = Field(min_length=1, max_length=240)
    product_url: str = Field(max_length=2000)
    quantity: int = Field(gt=0, le=9999)
    estimated_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    purpose: str = Field(min_length=1, max_length=3000)

    @field_validator("estimated_amount", mode="before")
    @classmethod
    def blank_amount_to_none(cls, value: object) -> object:
        return _optional_text(value)

    @field_validator("product_url")
    @classmethod
    def product_url_is_https(cls, value: str) -> str:
        return _https_url(value) or value


class CreateSettlementRequestCommand(BaseModel):
    requester_slack_user_id: str
    department_id: str
    budget_node_id: str
    assignee_slack_user_id: str
    source_conversation_id: str | None = None
    subject: str = Field(min_length=1, max_length=240)
    vendor: str = Field(min_length=1, max_length=240)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    purpose: str = Field(min_length=1, max_length=3000)
    evidence_folder_url: str | None = Field(default=None, max_length=2000)

    @field_validator("evidence_folder_url", mode="before")
    @classmethod
    def blank_folder_to_none(cls, value: object) -> object:
        return _optional_text(value)

    @field_validator("evidence_folder_url")
    @classmethod
    def folder_url_is_https(cls, value: str | None) -> str | None:
        return _https_url(value)
