"""Create the database-backed ERP ledger.

Revision ID: 20260814_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expense_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reference_number", sa.String(length=40), nullable=False),
        sa.Column("applicant_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("approval_channel_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("current_approver_slack_user_ids", sa.JSON(), nullable=False),
        sa.Column("slack_message_ts", sa.String(length=32), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_number"),
    )
    op.create_index(
        "ix_expense_requests_applicant_slack_user_id",
        "expense_requests",
        ["applicant_slack_user_id"],
    )
    op.create_index(
        "ix_expense_requests_approval_channel_id", "expense_requests", ["approval_channel_id"]
    )
    op.create_index("ix_expense_requests_status", "expense_requests", ["status"])
    op.create_table(
        "work_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reference_number", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("requester_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("assignee_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("slack_message_ts", sa.String(length=32), nullable=True),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_number"),
    )
    op.create_index("ix_work_requests_kind", "work_requests", ["kind"])
    op.create_index(
        "ix_work_requests_requester_slack_user_id",
        "work_requests",
        ["requester_slack_user_id"],
    )
    op.create_index(
        "ix_work_requests_assignee_slack_user_id",
        "work_requests",
        ["assignee_slack_user_id"],
    )
    op.create_index("ix_work_requests_channel_id", "work_requests", ["channel_id"])
    op.create_index("ix_work_requests_status", "work_requests", ["status"])
    op.create_table(
        "role_assignments",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_by_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope", "role_id", "slack_user_id"),
    )
    op.create_table(
        "approval_routes",
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.String(length=128), nullable=False),
        sa.Column("budget_program_id", sa.String(length=128), nullable=False),
        sa.Column("approval_channel_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("department_id", "category_id"),
    )
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("audit_channel_id", sa.String(length=32), nullable=True),
        sa.Column("alerts_channel_id", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_slack_user_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "operating_channels",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("registered_by_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("channel_id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table(
        "expense_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("actor_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["expense_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "sequence", name="uq_expense_event_sequence"),
    )
    op.create_index(
        "ix_expense_events_request_sequence", "expense_events", ["request_id", "sequence"]
    )
    op.create_table(
        "work_request_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("actor_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["work_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "sequence", name="uq_work_request_event_sequence"),
    )
    op.create_index(
        "ix_work_request_events_request_sequence",
        "work_request_events",
        ["request_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_request_events_request_sequence", table_name="work_request_events")
    op.drop_table("work_request_events")
    op.drop_index("ix_expense_events_request_sequence", table_name="expense_events")
    op.drop_table("expense_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("operating_channels")
    op.drop_table("system_settings")
    op.drop_table("approval_routes")
    op.drop_table("role_assignments")
    op.drop_index("ix_work_requests_status", table_name="work_requests")
    op.drop_index("ix_work_requests_channel_id", table_name="work_requests")
    op.drop_index("ix_work_requests_assignee_slack_user_id", table_name="work_requests")
    op.drop_index("ix_work_requests_requester_slack_user_id", table_name="work_requests")
    op.drop_index("ix_work_requests_kind", table_name="work_requests")
    op.drop_table("work_requests")
    op.drop_index("ix_expense_requests_status", table_name="expense_requests")
    op.drop_index("ix_expense_requests_approval_channel_id", table_name="expense_requests")
    op.drop_index("ix_expense_requests_applicant_slack_user_id", table_name="expense_requests")
    op.drop_table("expense_requests")
