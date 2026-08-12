"""runtime approval configuration

Revision ID: 79d0a79f4d6b
Revises: cbc8103c81d3
Create Date: 2026-08-13 00:00:00
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "79d0a79f4d6b"
down_revision: str | None = "cbc8103c81d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("departments") as batch_op:
        batch_op.alter_column(
            "approval_channel_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )

    with op.batch_alter_table("approval_step_definitions") as batch_op:
        batch_op.alter_column(
            "approver_type",
            existing_type=sa.String(length=32),
            nullable=True,
        )
        batch_op.alter_column(
            "approver_reference",
            existing_type=sa.String(length=120),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "approval_policy",
                sa.String(length=16),
                server_default="ANY",
                nullable=False,
            )
        )

    with op.batch_alter_table("approval_steps") as batch_op:
        batch_op.alter_column(
            "approver_type",
            existing_type=sa.String(length=32),
            nullable=True,
        )
        batch_op.alter_column(
            "approver_reference",
            existing_type=sa.String(length=120),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "approval_policy",
                sa.String(length=16),
                server_default="ANY",
                nullable=False,
            )
        )

    op.create_table(
        "approval_step_definition_approvers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_definition_id", sa.String(length=160), nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["step_definition_id"],
            ["approval_step_definitions.id"],
            ondelete="CASCADE",
            name=op.f(
                "fk_approval_step_definition_approvers_step_definition_id_approval_step_definitions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_step_definition_approvers")),
        sa.UniqueConstraint(
            "step_definition_id",
            "slack_user_id",
            name=op.f("uq_approval_step_definition_approvers_step_definition_id"),
        ),
    )
    op.create_table(
        "approval_step_approvers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_step_id", sa.Uuid(), nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_step_id"],
            ["approval_steps.id"],
            ondelete="CASCADE",
            name=op.f("fk_approval_step_approvers_approval_step_id_approval_steps"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_step_approvers")),
        sa.UniqueConstraint(
            "approval_step_id",
            "slack_user_id",
            name=op.f("uq_approval_step_approvers_approval_step_id"),
        ),
    )
    _migrate_legacy_approvers()
    _demote_legacy_admin_placeholders()


def _migrate_legacy_approvers() -> None:
    connection = op.get_bind()
    now = datetime.now(UTC)
    definitions = connection.execute(
        sa.text(
            "SELECT id, approver_type, approver_reference "
            "FROM approval_step_definitions "
            "WHERE approver_type = 'SLACK_USER' AND approver_reference IS NOT NULL"
        )
    )
    for definition_id, _, slack_user_id in definitions:
        if slack_user_id.startswith("U_REPLACE"):
            continue
        connection.execute(
            sa.text(
                "INSERT INTO approval_step_definition_approvers "
                "(id, step_definition_id, slack_user_id, created_at, updated_at) "
                "VALUES (:id, :step_definition_id, :slack_user_id, :created_at, :updated_at)"
            ),
            {
                "id": uuid.uuid4().hex,
                "step_definition_id": definition_id,
                "slack_user_id": slack_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    instances = connection.execute(
        sa.text(
            "SELECT id, approver_type, approver_reference "
            "FROM approval_steps "
            "WHERE approver_type = 'SLACK_USER' AND approver_reference IS NOT NULL"
        )
    )
    for approval_step_id, _, slack_user_id in instances:
        if slack_user_id.startswith("U_REPLACE"):
            continue
        connection.execute(
            sa.text(
                "INSERT INTO approval_step_approvers "
                "(id, approval_step_id, slack_user_id, created_at, updated_at) "
                "VALUES (:id, :approval_step_id, :slack_user_id, :created_at, :updated_at)"
            ),
            {
                "id": uuid.uuid4().hex,
                "approval_step_id": approval_step_id,
                "slack_user_id": slack_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _demote_legacy_admin_placeholders() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE user_profiles SET role = 'REQUESTER' "
            "WHERE role = 'SYSTEM_ADMIN' AND slack_user_id LIKE 'U_REPLACE%'"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE approval_step_definitions SET approver_type = 'SLACK_USER', "
            "approver_reference = 'U_UNASSIGNED' WHERE approver_reference IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE approval_steps SET approver_type = 'SLACK_USER', "
            "approver_reference = 'U_UNASSIGNED' WHERE approver_reference IS NULL"
        )
    )
    op.drop_table("approval_step_approvers")
    op.drop_table("approval_step_definition_approvers")
    with op.batch_alter_table("approval_steps") as batch_op:
        batch_op.drop_column("approval_policy")
        batch_op.alter_column(
            "approver_reference",
            existing_type=sa.String(length=120),
            nullable=False,
        )
        batch_op.alter_column(
            "approver_type",
            existing_type=sa.String(length=32),
            nullable=False,
        )
    with op.batch_alter_table("approval_step_definitions") as batch_op:
        batch_op.drop_column("approval_policy")
        batch_op.alter_column(
            "approver_reference",
            existing_type=sa.String(length=120),
            nullable=False,
        )
        batch_op.alter_column(
            "approver_type",
            existing_type=sa.String(length=32),
            nullable=False,
        )
    connection.execute(
        sa.text(
            "UPDATE departments SET approval_channel_id = 'C_UNASSIGNED' "
            "WHERE approval_channel_id IS NULL"
        )
    )
    with op.batch_alter_table("departments") as batch_op:
        batch_op.alter_column(
            "approval_channel_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
