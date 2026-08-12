import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals.configuration import (
    ApprovalConfigurationService,
    ApprovalStepConfiguration,
)
from app.approvals.resolver import ApprovalRuleResolver
from app.config.settings import Settings
from app.db.enums import UserRole
from app.db.models import ApprovalStepDefinitionApprover, Department, UserProfile
from app.db.seed import seed_database
from app.exceptions import ApprovalPermissionError, ConfigurationError


def bootstrap_settings(admin_id: str = "U_BOOTSTRAP_ADMIN") -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        auto_create_schema=False,
        seed_configuration=False,
        bootstrap_system_admin_slack_user_ids=admin_id,
    )


def test_runtime_rule_supports_multiple_eligible_approvers(session: Session) -> None:
    settings = bootstrap_settings()
    seed_database(session, settings)
    session.commit()

    service = ApprovalConfigurationService(session)
    rule = service.save_rule(
        actor_slack_user_id="U_BOOTSTRAP_ADMIN",
        department_id="department_1",
        category_id="airfare",
        approval_channel_id="C_PRIVATE_APPROVAL",
        steps=[
            ApprovalStepConfiguration(
                name_en="Professor Approval",
                name_ko="교수 승인",
                approver_slack_user_ids=("U_PROFESSOR_A", "U_PROFESSOR_B"),
            ),
            ApprovalStepConfiguration(
                name_en="Administration Review",
                name_ko="행정 검토",
                approver_slack_user_ids=("U_ADMIN_A", "U_ADMIN_B"),
            ),
        ],
    )
    session.commit()

    assert rule.workflow.version == 2
    assert session.get(Department, "department_1").approval_channel_id == "C_PRIVATE_APPROVAL"
    assert [
        [approver.slack_user_id for approver in step.approvers] for step in rule.workflow.steps
    ] == [["U_PROFESSOR_A", "U_PROFESSOR_B"], ["U_ADMIN_A", "U_ADMIN_B"]]
    resolved = ApprovalRuleResolver(session).resolve_workflow(
        "department_1", "student_support", "airfare"
    )
    assert resolved.id == rule.workflow.id


def test_incomplete_rule_can_be_saved_but_cannot_accept_requests(session: Session) -> None:
    settings = bootstrap_settings()
    seed_database(session, settings)
    session.commit()

    ApprovalConfigurationService(session).save_rule(
        actor_slack_user_id="U_BOOTSTRAP_ADMIN",
        department_id="department_1",
        category_id="lodging",
        approval_channel_id="C_PRIVATE_APPROVAL",
        steps=[
            ApprovalStepConfiguration(
                name_en="Professor Approval",
                name_ko="교수 승인",
                approver_slack_user_ids=(),
            )
        ],
    )
    session.commit()

    with pytest.raises(ConfigurationError):
        ApprovalRuleResolver(session).resolve_workflow("department_1", "student_support", "lodging")


def test_non_admin_cannot_change_runtime_rule(session: Session) -> None:
    settings = bootstrap_settings()
    seed_database(session, settings)
    session.add(
        UserProfile(
            slack_user_id="U_REQUESTER",
            display_name="Requester",
            role=UserRole.REQUESTER,
        )
    )
    session.commit()

    with pytest.raises(ApprovalPermissionError):
        ApprovalConfigurationService(session).save_rule(
            actor_slack_user_id="U_REQUESTER",
            department_id="department_1",
            category_id="supplies",
            approval_channel_id="C_PRIVATE_APPROVAL",
            steps=[
                ApprovalStepConfiguration(
                    name_en="Professor Approval",
                    name_ko="교수 승인",
                    approver_slack_user_ids=("U_PROFESSOR",),
                )
            ],
        )

    assert session.get(Department, "department_1").approval_channel_id is None


def test_bootstrap_admin_is_only_applied_when_no_admin_exists(session: Session) -> None:
    settings = bootstrap_settings()
    seed_database(session, settings)
    session.commit()

    ApprovalConfigurationService(session).replace_system_admins(
        "U_BOOTSTRAP_ADMIN", ["U_RUNTIME_ADMIN"]
    )
    session.commit()
    seed_database(session, settings)
    session.commit()

    assert session.get(UserProfile, "U_BOOTSTRAP_ADMIN").role == UserRole.REQUESTER
    assert session.get(UserProfile, "U_RUNTIME_ADMIN").role == UserRole.SYSTEM_ADMIN
    assert ApprovalConfigurationService(session).system_admin_ids() == ["U_RUNTIME_ADMIN"]


def test_seed_contains_no_hard_coded_step_approvers(session: Session, settings: Settings) -> None:
    seed_database(session, settings)
    session.commit()

    assert session.scalar(select(func.count()).select_from(ApprovalStepDefinitionApprover)) == 0
    assert all(
        department.approval_channel_id is None for department in session.scalars(select(Department))
    )
