"""initial schema

Revision ID: cbc8103c81d3
Revises:
Create Date: 2026-08-12 21:20:03.104494
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cbc8103c81d3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_workflow_definitions",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("name_en", sa.String(length=160), nullable=False),
        sa.Column("name_ko", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_workflow_definitions")),
    )
    op.create_table(
        "budget_programs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_programs")),
    )
    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column("approval_channel_id", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
    )
    op.create_table(
        "approval_step_definitions",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("workflow_definition_id", sa.String(length=120), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column(
            "approver_type",
            sa.Enum(
                "SLACK_USER", "DEPARTMENT_ROLE", name="approvertype", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("approver_reference", sa.String(length=120), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["approval_workflow_definitions.id"],
            name=op.f(
                "fk_approval_step_definitions_workflow_definition_id_approval_workflow_definitions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_step_definitions")),
        sa.UniqueConstraint(
            "workflow_definition_id",
            "step_order",
            name=op.f("uq_approval_step_definitions_workflow_definition_id"),
        ),
    )
    op.create_table(
        "expense_categories",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("budget_program_id", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["budget_program_id"],
            ["budget_programs.id"],
            name=op.f("fk_expense_categories_budget_program_id_budget_programs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_categories")),
    )
    op.create_table(
        "user_profiles",
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "REQUESTER",
                "APPROVER",
                "SYSTEM_ADMIN",
                name="userrole",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "applicant_type",
            sa.Enum("STUDENT", "OTHER", name="applicanttype", native_enum=False, length=32),
            nullable=True,
        ),
        sa.Column("student_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_user_profiles_department_id_departments"),
        ),
        sa.PrimaryKeyConstraint("slack_user_id", name=op.f("pk_user_profiles")),
    )
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("budget_program_id", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_definition_id", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["budget_program_id"],
            ["budget_programs.id"],
            name=op.f("fk_approval_rules_budget_program_id_budget_programs"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["expense_categories.id"],
            name=op.f("fk_approval_rules_category_id_expense_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_approval_rules_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_definition_id"],
            ["approval_workflow_definitions.id"],
            name=op.f("fk_approval_rules_workflow_definition_id_approval_workflow_definitions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_rules")),
        sa.UniqueConstraint(
            "department_id",
            "budget_program_id",
            "category_id",
            name=op.f("uq_approval_rules_department_id"),
        ),
    )
    op.create_table(
        "evidence_requirement_definitions",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_key", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column(
            "timing",
            sa.Enum("PRE", "POST", name="evidencetiming", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "requirement",
            sa.Enum(
                "REQUIRED",
                "OPTIONAL",
                name="evidencerequirementlevel",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("allow_waiver", sa.Boolean(), nullable=False),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("description_ko", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["expense_categories.id"],
            name=op.f("fk_evidence_requirement_definitions_category_id_expense_categories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_requirement_definitions")),
        sa.UniqueConstraint(
            "category_id",
            "evidence_key",
            name=op.f("uq_evidence_requirement_definitions_category_id"),
        ),
    )
    op.create_table(
        "expense_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reference_number", sa.String(length=32), nullable=False),
        sa.Column("applicant_slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("applicant_display_name", sa.String(length=160), nullable=False),
        sa.Column(
            "applicant_type",
            sa.Enum("STUDENT", "OTHER", name="applicanttype", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("student_id", sa.String(length=64), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("budget_program_id", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("vendor", sa.String(length=240), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("evidence_folder_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "IN_APPROVAL",
                "CHANGES_REQUESTED",
                "REJECTED",
                "APPROVED",
                "APPROVED_PENDING_POST_EVIDENCE",
                "COMPLETED",
                name="requeststatus",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("current_step_order", sa.Integer(), nullable=True),
        sa.Column("approval_channel_id", sa.String(length=64), nullable=False),
        sa.Column("approval_message_ts", sa.String(length=64), nullable=True),
        sa.Column("workflow_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["applicant_slack_user_id"],
            ["user_profiles.slack_user_id"],
            name=op.f("fk_expense_requests_applicant_slack_user_id_user_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["budget_program_id"],
            ["budget_programs.id"],
            name=op.f("fk_expense_requests_budget_program_id_budget_programs"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["expense_categories.id"],
            name=op.f("fk_expense_requests_category_id_expense_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_expense_requests_department_id_departments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_requests")),
    )
    op.create_index(
        op.f("ix_expense_requests_reference_number"),
        "expense_requests",
        ["reference_number"],
        unique=True,
    )
    op.create_table(
        "approval_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("step_definition_id", sa.String(length=160), nullable=True),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column(
            "approver_type",
            sa.Enum(
                "SLACK_USER", "DEPARTMENT_ROLE", name="approvertype", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("approver_reference", sa.String(length=120), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "WAITING",
                "PENDING",
                "APPROVED",
                "CHANGES_REQUESTED",
                "REJECTED",
                name="approvalstepstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("acted_by_slack_user_id", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["expense_requests.id"],
            name=op.f("fk_approval_steps_request_id_expense_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_definition_id"],
            ["approval_step_definitions.id"],
            name=op.f("fk_approval_steps_step_definition_id_approval_step_definitions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_steps")),
        sa.UniqueConstraint("request_id", "step_order", name=op.f("uq_approval_steps_request_id")),
    )
    op.create_table(
        "evidence_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_definition_id", sa.String(length=96), nullable=True),
        sa.Column("requirement_key", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_ko", sa.String(length=120), nullable=False),
        sa.Column(
            "timing",
            sa.Enum("PRE", "POST", name="evidencetiming", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "requirement",
            sa.Enum(
                "REQUIRED",
                "OPTIONAL",
                name="evidencerequirementlevel",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("allow_waiver", sa.Boolean(), nullable=False),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("description_ko", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "MISSING",
                "SUBMITTED",
                name="evidencesubmissionstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["expense_requests.id"],
            name=op.f("fk_evidence_submissions_request_id_expense_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_definition_id"],
            ["evidence_requirement_definitions.id"],
            name=op.f(
                "fk_evidence_submissions_requirement_definition_id_evidence_requirement_definitions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_submissions")),
        sa.UniqueConstraint(
            "request_id", "requirement_key", name=op.f("uq_evidence_submissions_request_id")
        ),
    )
    op.create_table(
        "approval_action_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "REQUEST_CREATED",
                "REQUEST_SUBMITTED",
                "EVIDENCE_SUBMITTED",
                "APPROVAL_STEP_APPROVED",
                "CHANGES_REQUESTED",
                "REQUEST_RESUBMITTED",
                "REQUEST_REJECTED",
                "POST_EVIDENCE_SUBMITTED",
                "REQUEST_COMPLETED",
                name="auditeventtype",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("actor_slack_user_id", sa.String(length=64), nullable=True),
        sa.Column("approval_step_id", sa.Uuid(), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_step_id"],
            ["approval_steps.id"],
            name=op.f("fk_approval_action_logs_approval_step_id_approval_steps"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["expense_requests.id"],
            name=op.f("fk_approval_action_logs_request_id_expense_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_action_logs")),
    )
    op.create_index(
        op.f("ix_approval_action_logs_request_id"),
        "approval_action_logs",
        ["request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_action_logs_request_id"), table_name="approval_action_logs")
    op.drop_table("approval_action_logs")
    op.drop_table("evidence_submissions")
    op.drop_table("approval_steps")
    op.drop_index(op.f("ix_expense_requests_reference_number"), table_name="expense_requests")
    op.drop_table("expense_requests")
    op.drop_table("evidence_requirement_definitions")
    op.drop_table("approval_rules")
    op.drop_table("user_profiles")
    op.drop_table("expense_categories")
    op.drop_table("approval_step_definitions")
    op.drop_table("departments")
    op.drop_table("budget_programs")
    op.drop_table("approval_workflow_definitions")
