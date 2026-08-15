from pydantic import BaseModel, Field, field_validator

from app.domain.enums import ApplicantType


class UpdateApplicantProfileCommand(BaseModel):
    slack_user_id: str = Field(min_length=1, max_length=32)
    applicant_type: ApplicantType
    applicant_identifier: str = Field(min_length=1, max_length=64)

    @field_validator("slack_user_id", "applicant_identifier", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("applicant_type")
    @classmethod
    def supported_applicant_type(cls, value: ApplicantType) -> ApplicantType:
        if value not in {ApplicantType.STUDENT, ApplicantType.PROFESSOR}:
            raise ValueError("Applicant profile must be student or professor")
        return value
