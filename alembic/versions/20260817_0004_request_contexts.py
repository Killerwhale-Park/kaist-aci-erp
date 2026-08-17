"""Add reusable Slack conversation request contexts.

Revision ID: 20260817_0004
Revises: 20260815_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0004"
down_revision: str | None = "20260815_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_contexts",
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("budget_node_id", sa.String(length=128), nullable=False),
        sa.Column("updated_by_slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
    )


def downgrade() -> None:
    op.drop_table("request_contexts")
