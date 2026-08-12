from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals.service import ApprovalService
from app.config.settings import Settings
from app.db.enums import (
    ApprovalStepStatus,
    ApproverType,
    AuditEventType,
    EvidenceRequirementLevel,
    EvidenceTiming,
    RequestStatus,
    UserRole,
)
from app.db.models import (
    ApprovalActionLog,
    ApprovalRule,
    ApprovalStepDefinition,
    ApprovalWorkflowDefinition,
    BudgetProgram,
    Department,
    EvidenceRequirementDefinition,
    ExpenseCategory,
    UserProfile,
)
from app.db.repository import ExpenseRequestRepository
from app.exceptions import (
    ApprovalPermissionError,
    AuditLogMutationError,
    DomainValidationError,
)
from app.expenses.schemas import (
    CreateExpenseCommand,
    EditExpenseCommand,
    EvidenceInput,
    PostEvidenceCommand,
)
from app.expenses.service import ExpenseService
from app.users.service import UserProfileService


def configure_policy(
    session: Session,
    step_count: int,
    *,
    approver_type: ApproverType = ApproverType.SLACK_USER,
    approver_reference: str | None = None,
    required_pre: bool = False,
    required_post: bool = False,
    optional_post: bool = False,
) -> list[str]:
    session.add_all(
        [
            Department(
                id="department_a",
                name_en="Department A",
                name_ko="학과 A",
                approval_channel_id="C_DEPARTMENT_A",
            ),
            Department(
                id="department_b",
                name_en="Department B",
                name_ko="학과 B",
                approval_channel_id="C_DEPARTMENT_B",
            ),
            BudgetProgram(
                id="student_support",
                name_en="Student Support Budget",
                name_ko="학생 지원 예산",
                is_available=True,
            ),
            ExpenseCategory(
                id="test_category",
                budget_program_id="student_support",
                name_en="Test Category",
                name_ko="테스트 항목",
            ),
            ApprovalWorkflowDefinition(
                id="test_workflow",
                name_en="Test Workflow",
                name_ko="테스트 승인 절차",
                version=1,
            ),
        ]
    )
    approvers: list[str] = []
    for step_order in range(1, step_count + 1):
        reference = approver_reference or f"U_APPROVER_{step_order}"
        approvers.append(reference)
        session.add(
            ApprovalStepDefinition(
                id=f"test_workflow.step_{step_order}",
                workflow_definition_id="test_workflow",
                step_order=step_order,
                name_en=f"Approval Step {step_order}",
                name_ko=f"승인 단계 {step_order}",
                approver_type=approver_type,
                approver_reference=reference,
                required=True,
            )
        )
    session.add(
        ApprovalRule(
            id="test_rule",
            department_id="department_a",
            budget_program_id="student_support",
            category_id="test_category",
            workflow_definition_id="test_workflow",
        )
    )
    evidence_order = 1
    if required_pre:
        session.add(
            EvidenceRequirementDefinition(
                id="test_category.required_pre",
                category_id="test_category",
                evidence_key="required_pre",
                name_en="Required Receipt",
                name_ko="필수 영수증",
                timing=EvidenceTiming.PRE,
                requirement=EvidenceRequirementLevel.REQUIRED,
                display_order=evidence_order,
            )
        )
        evidence_order += 1
    else:
        session.add(
            EvidenceRequirementDefinition(
                id="test_category.optional_pre",
                category_id="test_category",
                evidence_key="optional_pre",
                name_en="Optional Receipt",
                name_ko="선택 영수증",
                timing=EvidenceTiming.PRE,
                requirement=EvidenceRequirementLevel.OPTIONAL,
                display_order=evidence_order,
            )
        )
        evidence_order += 1
    if required_post:
        session.add(
            EvidenceRequirementDefinition(
                id="test_category.required_post",
                category_id="test_category",
                evidence_key="required_post",
                name_en="Required Post Evidence",
                name_ko="필수 사후 증빙",
                timing=EvidenceTiming.POST,
                requirement=EvidenceRequirementLevel.REQUIRED,
                display_order=evidence_order,
            )
        )
        evidence_order += 1
    if optional_post:
        session.add(
            EvidenceRequirementDefinition(
                id="test_category.optional_post",
                category_id="test_category",
                evidence_key="optional_post",
                name_en="Optional Post Evidence",
                name_ko="선택 사후 증빙",
                timing=EvidenceTiming.POST,
                requirement=EvidenceRequirementLevel.OPTIONAL,
                display_order=evidence_order,
            )
        )
    session.commit()
    return approvers


def create_request(
    session: Session,
    settings: Settings,
    *,
    applicant: str = "U_STUDENT",
    evidence: dict[str, EvidenceInput] | None = None,
):
    service = ExpenseService(session, UserProfileService(session, settings))
    request = service.create_and_submit(
        CreateExpenseCommand(
            applicant_slack_user_id=applicant,
            applicant_display_name="Student One",
            department_id="department_a",
            applicant_type="STUDENT",
            student_id="202500001",
            budget_program_id="student_support",
            category_id="test_category",
            amount=Decimal("84320"),
            vendor="Example Store",
            payment_date=date(2026, 8, 12),
            purpose="Research equipment",
            evidence_folder_url="https://drive.google.com/drive/folders/example",
            evidence=evidence or {},
        )
    )
    session.commit()
    return request


@pytest.mark.parametrize("step_count", [1, 2, 3])
def test_generic_n_step_approval_completes(
    session: Session, settings: Settings, step_count: int
) -> None:
    approvers = configure_policy(session, step_count)
    request = create_request(session, settings)

    assert request.status == RequestStatus.IN_APPROVAL
    for index, approver in enumerate(approvers, start=1):
        ApprovalService(session).approve(request.id, approver)
        session.commit()
        if index < step_count:
            assert request.status == RequestStatus.IN_APPROVAL
            assert request.current_step_order == index + 1

    assert request.status == RequestStatus.COMPLETED
    assert request.current_step_order is None
    assert [step.status for step in request.approval_steps] == [
        ApprovalStepStatus.APPROVED
    ] * step_count


def test_unauthorized_user_cannot_change_state(session: Session, settings: Settings) -> None:
    configure_policy(session, 1)
    request = create_request(session, settings)
    audit_count_before = session.scalar(select(func.count()).select_from(ApprovalActionLog))

    with pytest.raises(ApprovalPermissionError):
        ApprovalService(session).approve(request.id, "U_ANOTHER_STUDENT")

    assert request.status == RequestStatus.IN_APPROVAL
    assert request.approval_steps[0].status == ApprovalStepStatus.PENDING
    assert session.scalar(select(func.count()).select_from(ApprovalActionLog)) == audit_count_before


def test_approver_from_another_department_is_denied(session: Session, settings: Settings) -> None:
    configure_policy(
        session,
        1,
        approver_type=ApproverType.DEPARTMENT_ROLE,
        approver_reference=UserRole.APPROVER.value,
    )
    session.add_all(
        [
            UserProfile(
                slack_user_id="U_DEPT_A_APPROVER",
                display_name="Department A Approver",
                department_id="department_a",
                role=UserRole.APPROVER,
            ),
            UserProfile(
                slack_user_id="U_DEPT_B_APPROVER",
                display_name="Department B Approver",
                department_id="department_b",
                role=UserRole.APPROVER,
            ),
        ]
    )
    session.commit()
    request = create_request(session, settings)

    with pytest.raises(ApprovalPermissionError):
        ApprovalService(session).approve(request.id, "U_DEPT_B_APPROVER")

    assert request.status == RequestStatus.IN_APPROVAL
    ApprovalService(session).approve(request.id, "U_DEPT_A_APPROVER")
    assert request.status == RequestStatus.COMPLETED


def test_reject_at_intermediate_step(session: Session, settings: Settings) -> None:
    approvers = configure_policy(session, 3)
    request = create_request(session, settings)

    ApprovalService(session).approve(request.id, approvers[0])
    ApprovalService(session).reject(request.id, approvers[1], "Policy mismatch")

    assert request.status == RequestStatus.REJECTED
    assert request.current_step_order is None
    assert request.approval_steps[0].status == ApprovalStepStatus.APPROVED
    assert request.approval_steps[1].status == ApprovalStepStatus.REJECTED
    assert request.approval_steps[2].status == ApprovalStepStatus.WAITING


def test_changes_resume_from_same_step(session: Session, settings: Settings) -> None:
    approvers = configure_policy(session, 2)
    request = create_request(session, settings)

    ApprovalService(session).approve(request.id, approvers[0])
    ApprovalService(session).request_changes(request.id, approvers[1], "Clearer receipt needed")
    assert request.status == RequestStatus.CHANGES_REQUESTED

    ExpenseService(session, UserProfileService(session, settings)).resubmit(
        request.id,
        "U_STUDENT",
        EditExpenseCommand(
            amount=Decimal("84320"),
            vendor="Example Store",
            payment_date=date(2026, 8, 12),
            purpose="Research equipment with clearer evidence",
            evidence_folder_url="https://drive.google.com/drive/folders/example",
            evidence={
                "optional_pre": EvidenceInput(url="https://drive.google.com/file/d/clearer-receipt")
            },
        ),
    )

    assert request.status == RequestStatus.IN_APPROVAL
    assert request.current_step_order == 2
    assert request.approval_steps[0].status == ApprovalStepStatus.APPROVED
    assert request.approval_steps[1].status == ApprovalStepStatus.PENDING
    ApprovalService(session).approve(request.id, approvers[1])
    assert request.status == RequestStatus.COMPLETED


def test_required_post_evidence_blocks_completion(session: Session, settings: Settings) -> None:
    approvers = configure_policy(session, 1, required_post=True)
    request = create_request(session, settings)

    ApprovalService(session).approve(request.id, approvers[0])
    assert request.status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE

    ExpenseService(session, UserProfileService(session, settings)).submit_post_evidence(
        request.id,
        "U_STUDENT",
        PostEvidenceCommand(
            evidence={
                "required_post": EvidenceInput(url="https://drive.google.com/file/d/boarding-pass")
            }
        ),
    )

    assert request.status == RequestStatus.COMPLETED
    events = list(
        session.scalars(
            select(ApprovalActionLog.event_type)
            .where(ApprovalActionLog.request_id == request.id)
            .order_by(ApprovalActionLog.created_at)
        )
    )
    assert AuditEventType.POST_EVIDENCE_SUBMITTED in events
    assert AuditEventType.REQUEST_COMPLETED in events


def test_optional_evidence_never_blocks(session: Session, settings: Settings) -> None:
    approvers = configure_policy(session, 1, optional_post=True)
    request = create_request(session, settings)

    assert request.status == RequestStatus.IN_APPROVAL
    ApprovalService(session).approve(request.id, approvers[0])

    assert request.status == RequestStatus.COMPLETED

    ExpenseService(session, UserProfileService(session, settings)).submit_post_evidence(
        request.id,
        "U_STUDENT",
        PostEvidenceCommand(
            evidence={
                "optional_post": EvidenceInput(url="https://drive.google.com/file/d/optional-badge")
            }
        ),
    )
    assert request.status == RequestStatus.COMPLETED


def test_missing_required_pre_evidence_blocks_submission(
    session: Session, settings: Settings
) -> None:
    configure_policy(session, 1, required_pre=True)

    with pytest.raises(DomainValidationError):
        create_request(session, settings)


def test_workflow_snapshot_is_immutable(session: Session, settings: Settings) -> None:
    approvers = configure_policy(session, 2)
    existing_request = create_request(session, settings)
    original_snapshot = list(existing_request.workflow_snapshot)

    session.add(
        ApprovalStepDefinition(
            id="test_workflow.step_3",
            workflow_definition_id="test_workflow",
            step_order=3,
            name_en="New Inspection",
            name_ko="새 검수",
            approver_type=ApproverType.SLACK_USER,
            approver_reference="U_NEW_INSPECTOR",
            required=True,
        )
    )
    session.commit()
    session.expire_all()
    new_request = create_request(session, settings, applicant="U_SECOND_STUDENT")

    stored_existing = ExpenseRequestRepository(session).get(existing_request.id)
    assert len(stored_existing.approval_steps) == 2
    assert stored_existing.workflow_snapshot == original_snapshot
    assert len(new_request.approval_steps) == 3
    ApprovalService(session).approve(stored_existing.id, approvers[0])
    ApprovalService(session).approve(stored_existing.id, approvers[1])
    assert stored_existing.status == RequestStatus.COMPLETED


def test_audit_log_is_append_only(session: Session, settings: Settings) -> None:
    configure_policy(session, 1)
    request = create_request(session, settings)
    audit_log = session.scalar(
        select(ApprovalActionLog).where(ApprovalActionLog.request_id == request.id)
    )
    assert audit_log is not None

    audit_log.event_metadata = {"changed": True}
    with pytest.raises(AuditLogMutationError):
        session.flush()
