from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ExpenseRequestRecord(Base):
    __tablename__ = "expense_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reference_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    applicant_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    approval_channel_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_work_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_requests.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    current_approver_slack_user_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    slack_message_ts: Mapped[str | None] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ExpenseEventRecord(Base):
    __tablename__ = "expense_events"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_expense_event_sequence"),
        Index("ix_expense_events_request_sequence", "request_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("expense_requests.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkRequestRecord(Base):
    __tablename__ = "work_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reference_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    requester_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    originator_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    assignee_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_requests.id", ondelete="SET NULL"), index=True
    )
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_step_order: Mapped[int | None] = mapped_column(Integer)
    current_approver_slack_user_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    slack_message_ts: Mapped[str | None] = mapped_column(String(32))
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class WorkRequestEventRecord(Base):
    __tablename__ = "work_request_events"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_work_request_event_sequence"),
        Index("ix_work_request_events_request_sequence", "request_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("work_requests.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoleAssignmentRecord(Base):
    __tablename__ = "role_assignments"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slack_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assigned_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ApplicantProfileRecord(Base):
    __tablename__ = "applicant_profiles"

    slack_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    applicant_type: Mapped[str] = mapped_column(String(24), nullable=False)
    applicant_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RequestContextRecord(Base):
    __tablename__ = "request_contexts"

    conversation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ApprovalRouteRecord(Base):
    __tablename__ = "approval_routes"

    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    budget_program_id: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SystemSettingsRecord(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    audit_channel_id: Mapped[str | None] = mapped_column(String(32))
    alerts_channel_id: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by_slack_user_id: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class OperatingChannelRecord(Base):
    __tablename__ = "operating_channels"

    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    registered_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
