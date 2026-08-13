from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.domain.enums import (
    ApplicantType,
    ApprovalStepStatus,
    EvidenceRequirementLevel,
    EvidenceSubmissionStatus,
    EvidenceTiming,
    RequestStatus,
    UserRole,
    WorkRequestKind,
    WorkRequestStatus,
)


@dataclass(frozen=True)
class Department:
    id: str
    name_en: str
    name_ko: str
    is_active: bool = True


@dataclass(frozen=True)
class BudgetProgram:
    id: str
    name_en: str
    name_ko: str
    is_available: bool
    is_active: bool = True


@dataclass(frozen=True)
class BudgetNode:
    id: str
    parent_id: str | None
    name_en: str
    name_ko: str
    expense_category_id: str | None = None

    @property
    def is_expense_category(self) -> bool:
        return self.expense_category_id is not None


@dataclass(frozen=True)
class EvidenceRequirementDefinition:
    id: str
    category_id: str
    evidence_key: str
    name_en: str
    name_ko: str
    timing: EvidenceTiming
    requirement: EvidenceRequirementLevel
    display_order: int
    allow_waiver: bool = False
    description_en: str | None = None
    description_ko: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class ExpenseCategory:
    id: str
    budget_program_id: str
    name_en: str
    name_ko: str
    evidence_requirements: tuple[EvidenceRequirementDefinition, ...] = ()
    budget_path_en: tuple[str, ...] = ()
    budget_path_ko: tuple[str, ...] = ()
    is_active: bool = True


@dataclass(frozen=True)
class ApprovalStepApprover:
    slack_user_id: str


@dataclass
class ApprovalStep:
    step_order: int
    name_en: str
    name_ko: str
    approvers: list[ApprovalStepApprover]
    status: ApprovalStepStatus = ApprovalStepStatus.WAITING
    acted_by_slack_user_id: str | None = None
    comment: str | None = None
    acted_at: datetime | None = None


@dataclass
class EvidenceSubmission:
    requirement_key: str
    name_en: str
    name_ko: str
    timing: EvidenceTiming
    requirement: EvidenceRequirementLevel
    display_order: int
    allow_waiver: bool = False
    description_en: str | None = None
    description_ko: str | None = None
    url: str | None = None
    note: str | None = None
    status: EvidenceSubmissionStatus = EvidenceSubmissionStatus.MISSING
    submitted_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalRuleStep:
    name_en: str
    name_ko: str
    approver_slack_user_ids: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalRule:
    department_id: str
    budget_program_id: str
    category_id: str
    approval_channel_id: str | None
    steps: tuple[ApprovalRuleStep, ...]
    version: int = 0

    @property
    def is_complete(self) -> bool:
        return bool(self.approval_channel_id and self.steps) and all(
            step.approver_slack_user_ids for step in self.steps
        )


@dataclass
class ExpenseRequest:
    id: str
    reference_number: str
    applicant_slack_user_id: str
    applicant_display_name: str
    applicant_type: ApplicantType
    applicant_identifier: str | None
    department_id: str
    budget_program_id: str
    category_id: str
    amount: Decimal
    currency: str
    vendor: str
    payment_date: date
    purpose: str
    evidence_folder_url: str | None
    status: RequestStatus
    current_step_order: int | None
    approval_channel_id: str
    workflow_snapshot: list[dict]
    evidence_snapshot: list[dict]
    submitted_at: datetime
    department: Department
    budget_program: BudgetProgram
    category: ExpenseCategory
    evidence_submissions: list[EvidenceSubmission] = field(default_factory=list)
    approval_steps: list[ApprovalStep] = field(default_factory=list)
    message_ts: str | None = None
    revision: int = 1


@dataclass(frozen=True)
class UserProfile:
    slack_user_id: str
    role: UserRole


@dataclass
class WorkRequest:
    id: str
    reference_number: str
    kind: WorkRequestKind
    requester_slack_user_id: str
    assignee_slack_user_id: str
    department_id: str
    channel_id: str
    subject: str
    purpose: str
    department: Department
    created_at: datetime
    quantity: int | None = None
    amount: Decimal | None = None
    vendor: str | None = None
    payment_date: date | None = None
    source_url: str | None = None
    evidence_folder_url: str | None = None
    status: WorkRequestStatus = WorkRequestStatus.OPEN
    completed_by_slack_user_id: str | None = None
    completed_at: datetime | None = None
    message_ts: str | None = None
