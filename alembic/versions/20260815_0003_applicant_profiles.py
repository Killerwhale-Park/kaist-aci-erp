"""Add stable applicant profiles.

Revision ID: 20260815_0003
Revises: 20260814_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0003"
down_revision: str | None = "20260814_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applicant_profiles",
        sa.Column("slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("applicant_type", sa.String(length=24), nullable=False),
        sa.Column("applicant_identifier", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("slack_user_id"),
    )


def downgrade() -> None:
    op.drop_table("applicant_profiles")
