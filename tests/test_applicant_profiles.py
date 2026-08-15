import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.application.profiles import UpdateApplicantProfileCommand
from app.domain.enums import ApplicantType
from app.ledger.repository import LedgerRepository
from app.ledger.tables import AuditEventRecord


@pytest.mark.asyncio
async def test_applicant_profile_is_persistent_and_audited_without_identifier(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)

    await ledger.save_applicant_profile(
        "U_APPLICANT",
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    stored = await ledger.applicant_profile("U_APPLICANT")

    assert stored is not None
    assert stored.applicant_type == ApplicantType.STUDENT
    assert stored.applicant_identifier == "202600001"

    async with database.session() as session:
        audit = await session.scalar(
            select(AuditEventRecord)
            .where(AuditEventRecord.event_type == "APPLICANT_PROFILE_UPDATED")
            .order_by(AuditEventRecord.id.desc())
        )
    assert audit is not None
    assert audit.detail == {"applicant_type": "STUDENT"}
    assert "202600001" not in str(audit.detail)


def test_applicant_profile_command_rejects_blank_identifier() -> None:
    with pytest.raises(ValidationError):
        UpdateApplicantProfileCommand(
            slack_user_id="U_APPLICANT",
            applicant_type=ApplicantType.PROFESSOR,
            applicant_identifier="   ",
        )
