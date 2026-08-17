from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.domain.enums import (
    ApplicantType,
    ApprovalStepStatus,
    BudgetFormScope,
    EvidenceRequirementLevel,
    EvidenceSubmissionStatus,
    EvidenceTiming,
    RequestStatus,
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
    form_scope: BudgetFormScope = BudgetFormScope.GLOBAL


@dataclass(frozen=True)
class BudgetNode:
    id: str
    parent_id: str | None
    name_en: str
    name_ko: str
    form_scope: BudgetFormScope | None = None


@dataclass(frozen=True)
class BudgetItemOption:
    """A selectable budget leaf, independent of its department-specific expense form."""

    id: str
    path_en: tuple[str, ...]
    path_ko: tuple[str, ...]


@dataclass(frozen=True)
class ExpenseForm:
    id: str
    name_en: str
    name_ko: str
    evidence_requirements: tuple[EvidenceRequirementDefinition, ...] = ()


@dataclass(frozen=True)
class BudgetFormMapping:
    budget_node_id: str
    form_id: str
    department_id: str | None = None


@dataclass(frozen=True)
class ApprovalWorkflowStepDefinition:
    id: str
    name_en: str
    name_ko: str
    approver_roles: tuple[str, ...]
    actor_binding: str | None = None


@dataclass(frozen=True)
class ApprovalWorkflowDefinition:
    id: str
    name_en: str
    name_ko: str
    steps: tuple[ApprovalWorkflowStepDefinition, ...]


@dataclass(frozen=True)
class BudgetWorkflowMapping:
    budget_node_id: str
    workflow_id: str
    department_id: str | None = None


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
    form_id: str
    form_name_en: str
    form_name_ko: str
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
    approver_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedApprovalWorkflow:
    id: str
    name_en: str
    name_ko: str
    steps: tuple[ApprovalRuleStep, ...]

    @property
    def is_complete(self) -> bool:
        return bool(self.steps) and all(step.approver_slack_user_ids for step in self.steps)


@dataclass(frozen=True)
class ApprovalRule:
    department_id: str
    budget_program_id: str
    category_id: str
    approval_channel_id: str | None
    steps: tuple[ApprovalRuleStep, ...]
    workflow_id: str | None = None
    workflow_name_en: str | None = None
    workflow_name_ko: str | None = None
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
    case_id: str | None
    source_work_request_id: str | None
    department: Department
    budget_program: BudgetProgram
    category: ExpenseCategory
    evidence_submissions: list[EvidenceSubmission] = field(default_factory=list)
    approval_steps: list[ApprovalStep] = field(default_factory=list)
    message_ts: str | None = None
    revision: int = 1

    @property
    def slack_locator(self) -> str:
        return self.id

    @property
    def current_approver_slack_user_ids(self) -> tuple[str, ...]:
        if self.status != RequestStatus.IN_APPROVAL or self.current_step_order is None:
            return ()
        step = next(
            (item for item in self.approval_steps if item.step_order == self.current_step_order),
            None,
        )
        if step is None:
            return ()
        return tuple(item.slack_user_id for item in step.approvers)


@dataclass(frozen=True)
class ApplicantProfile:
    """Stable applicant identity reused across expense submissions."""

    slack_user_id: str
    applicant_type: ApplicantType
    applicant_identifier: str


@dataclass(frozen=True)
class RequestContext:
    """Reusable request defaults attached to a Slack conversation, not an approval route."""

    conversation_id: str
    department_id: str
    budget_node_id: str


@dataclass
class WorkRequest:
    id: str
    reference_number: str
    kind: WorkRequestKind
    requester_slack_user_id: str
    originator_slack_user_id: str
    assignee_slack_user_id: str
    case_id: str
    parent_request_id: str | None
    department_id: str
    channel_id: str
    source_conversation_id: str | None
    subject: str
    purpose: str
    department: Department
    created_at: datetime
    workflow_snapshot: list[dict]
    approval_steps: list[ApprovalStep]
    current_step_order: int | None
    budget_program_id: str | None = None
    budget_node_id: str | None = None
    budget_node_path: tuple[str, ...] = ()
    budget_path_en: tuple[str, ...] = ()
    budget_path_ko: tuple[str, ...] = ()
    quantity: int | None = None
    amount: Decimal | None = None
    vendor: str | None = None
    payment_date: date | None = None
    source_url: str | None = None
    evidence_folder_url: str | None = None
    status: WorkRequestStatus = WorkRequestStatus.OPEN
    completed_by_slack_user_id: str | None = None
    completed_at: datetime | None = None
    successor_type: str | None = None
    successor_id: str | None = None
    rejection_reason: str | None = None
    message_ts: str | None = None

    @property
    def slack_locator(self) -> str:
        return self.id

    @property
    def current_approver_slack_user_ids(self) -> tuple[str, ...]:
        if self.status != WorkRequestStatus.IN_APPROVAL or self.current_step_order is None:
            return ()
        step = next(
            (item for item in self.approval_steps if item.step_order == self.current_step_order),
            None,
        )
        if step is None:
            return ()
        return tuple(item.slack_user_id for item in step.approvers)
