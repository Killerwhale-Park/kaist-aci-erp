from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.enums import ApplicantType


class EvidenceInput(BaseModel):
    url: str | None = None
    note: str | None = None

    @field_validator("url", "note", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


class CreateExpenseCommand(BaseModel):
    applicant_slack_user_id: str
    applicant_display_name: str
    department_id: str
    applicant_type: ApplicantType
    student_id: str | None = None
    budget_program_id: str
    category_id: str
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = "KRW"
    vendor: str = Field(min_length=1, max_length=240)
    payment_date: date
    purpose: str = Field(min_length=1, max_length=3000)
    evidence_folder_url: str | None = None
    evidence: dict[str, EvidenceInput] = Field(default_factory=dict)

    @field_validator("student_id", "evidence_folder_url", mode="before")
    @classmethod
    def optional_blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def student_has_student_id(self) -> "CreateExpenseCommand":
        if self.applicant_type == ApplicantType.STUDENT and not self.student_id:
            raise ValueError("Student ID is required for student applicants")
        return self


class EditExpenseCommand(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    vendor: str = Field(min_length=1, max_length=240)
    payment_date: date
    purpose: str = Field(min_length=1, max_length=3000)
    evidence_folder_url: str | None = None
    evidence: dict[str, EvidenceInput] = Field(default_factory=dict)

    @field_validator("evidence_folder_url", mode="before")
    @classmethod
    def blank_folder_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


class PostEvidenceCommand(BaseModel):
    evidence: dict[str, EvidenceInput]
