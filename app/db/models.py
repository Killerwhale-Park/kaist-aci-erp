from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import (
    ApplicantType,
    ApprovalStepStatus,
    ApproverType,
    AuditEventType,
    EvidenceRequirementLevel,
    EvidenceSubmissionStatus,
    EvidenceTiming,
    RequestStatus,
    UserRole,
)
from app.exceptions import AuditLogMutationError


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_column(enum_type: type, length: int = 64) -> SAEnum:
    return SAEnum(enum_type, native_enum=False, length=length, validate_strings=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    approval_channel_id: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BudgetProgram(Base, TimestampMixin):
    __tablename__ = "budget_programs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    is_available: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ExpenseCategory(Base, TimestampMixin):
    __tablename__ = "expense_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    budget_program_id: Mapped[str] = mapped_column(ForeignKey("budget_programs.id"))
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    budget_program: Mapped[BudgetProgram] = relationship()
    evidence_requirements: Mapped[list[EvidenceRequirementDefinition]] = relationship(
        back_populates="category", order_by="EvidenceRequirementDefinition.display_order"
    )


class EvidenceRequirementDefinition(Base, TimestampMixin):
    __tablename__ = "evidence_requirement_definitions"
    __table_args__ = (UniqueConstraint("category_id", "evidence_key"),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("expense_categories.id"))
    evidence_key: Mapped[str] = mapped_column(String(64))
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    timing: Mapped[EvidenceTiming] = mapped_column(enum_column(EvidenceTiming, 16))
    requirement: Mapped[EvidenceRequirementLevel] = mapped_column(
        enum_column(EvidenceRequirementLevel, 16)
    )
    allow_waiver: Mapped[bool] = mapped_column(Boolean, default=False)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped[ExpenseCategory] = relationship(back_populates="evidence_requirements")


class ApprovalWorkflowDefinition(Base, TimestampMixin):
    __tablename__ = "approval_workflow_definitions"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(160))
    name_ko: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    steps: Mapped[list[ApprovalStepDefinition]] = relationship(
        back_populates="workflow", order_by="ApprovalStepDefinition.step_order"
    )


class ApprovalStepDefinition(Base, TimestampMixin):
    __tablename__ = "approval_step_definitions"
    __table_args__ = (UniqueConstraint("workflow_definition_id", "step_order"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    workflow_definition_id: Mapped[str] = mapped_column(
        ForeignKey("approval_workflow_definitions.id")
    )
    step_order: Mapped[int] = mapped_column(Integer)
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    approver_type: Mapped[ApproverType] = mapped_column(enum_column(ApproverType, 32))
    approver_reference: Mapped[str] = mapped_column(String(120))
    required: Mapped[bool] = mapped_column(Boolean, default=True)

    workflow: Mapped[ApprovalWorkflowDefinition] = relationship(back_populates="steps")


class ApprovalRule(Base, TimestampMixin):
    __tablename__ = "approval_rules"
    __table_args__ = (UniqueConstraint("department_id", "budget_program_id", "category_id"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    budget_program_id: Mapped[str] = mapped_column(ForeignKey("budget_programs.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("expense_categories.id"))
    workflow_definition_id: Mapped[str] = mapped_column(
        ForeignKey("approval_workflow_definitions.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    workflow: Mapped[ApprovalWorkflowDefinition] = relationship()


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    slack_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole, 32), default=UserRole.REQUESTER)
    applicant_type: Mapped[ApplicantType | None] = mapped_column(
        enum_column(ApplicantType, 32), nullable=True
    )
    student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExpenseRequest(Base, TimestampMixin):
    __tablename__ = "expense_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    applicant_slack_user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.slack_user_id"))
    applicant_display_name: Mapped[str] = mapped_column(String(160))
    applicant_type: Mapped[ApplicantType] = mapped_column(enum_column(ApplicantType, 32))
    student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    budget_program_id: Mapped[str] = mapped_column(ForeignKey("budget_programs.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("expense_categories.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="KRW")
    vendor: Mapped[str] = mapped_column(String(240))
    payment_date: Mapped[date] = mapped_column(Date)
    purpose: Mapped[str] = mapped_column(Text)
    evidence_folder_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        enum_column(RequestStatus, 48), default=RequestStatus.DRAFT
    )
    current_step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_channel_id: Mapped[str] = mapped_column(String(64))
    approval_message_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    department: Mapped[Department] = relationship()
    budget_program: Mapped[BudgetProgram] = relationship()
    category: Mapped[ExpenseCategory] = relationship()
    evidence_submissions: Mapped[list[EvidenceSubmission]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="EvidenceSubmission.display_order",
    )
    approval_steps: Mapped[list[ApprovalStep]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ApprovalStep.step_order",
    )
    audit_logs: Mapped[list[ApprovalActionLog]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class EvidenceSubmission(Base, TimestampMixin):
    __tablename__ = "evidence_submissions"
    __table_args__ = (UniqueConstraint("request_id", "requirement_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_requests.id", ondelete="CASCADE")
    )
    requirement_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence_requirement_definitions.id"), nullable=True
    )
    requirement_key: Mapped[str] = mapped_column(String(64))
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    timing: Mapped[EvidenceTiming] = mapped_column(enum_column(EvidenceTiming, 16))
    requirement: Mapped[EvidenceRequirementLevel] = mapped_column(
        enum_column(EvidenceRequirementLevel, 16)
    )
    allow_waiver: Mapped[bool] = mapped_column(Boolean, default=False)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EvidenceSubmissionStatus] = mapped_column(
        enum_column(EvidenceSubmissionStatus, 16), default=EvidenceSubmissionStatus.MISSING
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped[ExpenseRequest] = relationship(back_populates="evidence_submissions")


class ApprovalStep(Base, TimestampMixin):
    __tablename__ = "approval_steps"
    __table_args__ = (UniqueConstraint("request_id", "step_order"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_requests.id", ondelete="CASCADE")
    )
    step_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_step_definitions.id"), nullable=True
    )
    step_order: Mapped[int] = mapped_column(Integer)
    name_en: Mapped[str] = mapped_column(String(120))
    name_ko: Mapped[str] = mapped_column(String(120))
    approver_type: Mapped[ApproverType] = mapped_column(enum_column(ApproverType, 32))
    approver_reference: Mapped[str] = mapped_column(String(120))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[ApprovalStepStatus] = mapped_column(enum_column(ApprovalStepStatus, 32))
    acted_by_slack_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped[ExpenseRequest] = relationship(back_populates="approval_steps")


class ApprovalActionLog(Base):
    __tablename__ = "approval_action_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_requests.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(enum_column(AuditEventType, 48))
    actor_slack_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_steps.id"), nullable=True
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    request: Mapped[ExpenseRequest] = relationship(back_populates="audit_logs")


@event.listens_for(ApprovalActionLog, "before_update")
@event.listens_for(ApprovalActionLog, "before_delete")
def prevent_audit_log_mutation(*_: object) -> None:
    raise AuditLogMutationError("Approval action logs are append-only")
