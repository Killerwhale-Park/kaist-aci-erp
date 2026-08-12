from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.enums import EvidenceRequirementLevel
from app.db.models import (
    ApprovalRule,
    ApprovalStepDefinition,
    Department,
    EvidenceRequirementDefinition,
)
from app.db.seed import seed_database


def test_seed_creates_four_departments_and_sample_rules(
    session: Session, settings: Settings
) -> None:
    seed_database(session, settings)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Department)) == 4
    assert session.scalar(select(func.count()).select_from(ApprovalRule)) == 16
    supplies_steps = session.scalar(
        select(func.count())
        .select_from(ApprovalStepDefinition)
        .where(ApprovalStepDefinition.workflow_definition_id == "wf_department_1_supplies_v1")
    )
    assert supplies_steps == 3


def test_unconfirmed_evidence_policy_is_seeded_as_optional(
    session: Session, settings: Settings
) -> None:
    seed_database(session, settings)
    session.commit()

    requirements = list(session.scalars(select(EvidenceRequirementDefinition)))
    assert requirements
    assert {item.requirement for item in requirements} == {EvidenceRequirementLevel.OPTIONAL}


def test_seed_is_idempotent(session: Session, settings: Settings) -> None:
    seed_database(session, settings)
    session.commit()
    seed_database(session, settings)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Department)) == 4
    assert session.scalar(select(func.count()).select_from(ApprovalRule)) == 16
