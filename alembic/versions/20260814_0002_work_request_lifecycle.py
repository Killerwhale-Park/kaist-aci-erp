"""Add approval, case lineage, and work-queue projections.

Revision ID: 20260814_0002
Revises: 20260814_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0002"
down_revision: str | None = "20260814_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("expense_requests", sa.Column("case_id", sa.String(length=36), nullable=True))
    op.add_column(
        "expense_requests",
        sa.Column("source_work_request_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("expense_requests") as batch_op:
        batch_op.create_foreign_key(
            "fk_expense_requests_source_work_request_id",
            "work_requests",
            ["source_work_request_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_expense_requests_case_id", "expense_requests", ["case_id"])
    op.create_index(
        "ix_expense_requests_source_work_request_id",
        "expense_requests",
        ["source_work_request_id"],
    )
    op.add_column(
        "work_requests",
        sa.Column("originator_slack_user_id", sa.String(length=32), nullable=True),
    )
    op.add_column("work_requests", sa.Column("case_id", sa.String(length=36), nullable=True))
    op.add_column(
        "work_requests", sa.Column("parent_request_id", sa.String(length=36), nullable=True)
    )
    op.add_column("work_requests", sa.Column("current_step_order", sa.Integer(), nullable=True))
    op.add_column(
        "work_requests",
        sa.Column("current_approver_slack_user_ids", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE work_requests SET originator_slack_user_id = requester_slack_user_id, "
        "case_id = id, current_approver_slack_user_ids = '[]' "
        "WHERE originator_slack_user_id IS NULL"
    )
    with op.batch_alter_table("work_requests") as batch_op:
        batch_op.alter_column("originator_slack_user_id", nullable=False)
        batch_op.alter_column("case_id", nullable=False)
        batch_op.alter_column("current_approver_slack_user_ids", nullable=False)
        batch_op.create_foreign_key(
            "fk_work_requests_parent_request_id",
            "work_requests",
            ["parent_request_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_work_requests_originator_slack_user_id",
        "work_requests",
        ["originator_slack_user_id"],
    )
    op.create_index("ix_work_requests_case_id", "work_requests", ["case_id"])
    op.create_index("ix_work_requests_parent_request_id", "work_requests", ["parent_request_id"])


def downgrade() -> None:
    op.drop_index("ix_work_requests_parent_request_id", table_name="work_requests")
    op.drop_index("ix_work_requests_case_id", table_name="work_requests")
    op.drop_index("ix_work_requests_originator_slack_user_id", table_name="work_requests")
    with op.batch_alter_table("work_requests") as batch_op:
        batch_op.drop_constraint("fk_work_requests_parent_request_id", type_="foreignkey")
        batch_op.drop_column("current_approver_slack_user_ids")
        batch_op.drop_column("current_step_order")
        batch_op.drop_column("parent_request_id")
        batch_op.drop_column("case_id")
        batch_op.drop_column("originator_slack_user_id")
    op.drop_index("ix_expense_requests_source_work_request_id", table_name="expense_requests")
    op.drop_index("ix_expense_requests_case_id", table_name="expense_requests")
    with op.batch_alter_table("expense_requests") as batch_op:
        batch_op.drop_constraint("fk_expense_requests_source_work_request_id", type_="foreignkey")
        batch_op.drop_column("source_work_request_id")
        batch_op.drop_column("case_id")
