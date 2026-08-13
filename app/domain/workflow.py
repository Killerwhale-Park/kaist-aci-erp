from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import (
    ApplicantType,
    ApprovalStepStatus,
    EvidenceRequirementLevel,
    EvidenceSubmissionStatus,
    EvidenceTiming,
    RequestStatus,
)
from app.domain.models import (
    ApprovalRule,
    ApprovalStep,
    ApprovalStepApprover,
    BudgetProgram,
    Department,
    EvidenceSubmission,
    ExpenseCategory,
    ExpenseRequest,
)
from app.exceptions import (
    ApprovalPermissionError,
    ConfigurationError,
    DomainError,
    DomainValidationError,
    InvalidStateTransitionError,
)
from app.expenses.evidence import (
    apply_evidence_value,
    required_post_evidence_complete,
    validate_https_url,
    validate_required_evidence,
)
from app.expenses.schemas import (
    CreateExpenseCommand,
    EditExpenseCommand,
    PostEvidenceCommand,
)

REQUEST_CREATED = "REQUEST_CREATED"
APPROVAL_STEP_APPROVED = "APPROVAL_STEP_APPROVED"
CHANGES_REQUESTED = "CHANGES_REQUESTED"
REQUEST_REJECTED = "REQUEST_REJECTED"
REQUEST_RESUBMITTED = "REQUEST_RESUBMITTED"
POST_EVIDENCE_SUBMITTED = "POST_EVIDENCE_SUBMITTED"


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_request_identity(now: datetime | None = None) -> tuple[str, str]:
    instant = now or utc_now()
    request_id = str(uuid.uuid4())
    reference = f"EXP-{instant:%Y%m%d}-{request_id[:8].upper()}"
    return request_id, reference


def created_event_data(
    command: CreateExpenseCommand,
    rule: ApprovalRule,
    *,
    department: Department,
    budget: BudgetProgram,
    category: ExpenseCategory,
    request_id: str | None = None,
    reference_number: str | None = None,
    submitted_at: datetime | None = None,
) -> dict[str, Any]:
    if not rule.is_complete or not rule.approval_channel_id:
        raise ConfigurationError("Approval rule is incomplete")
    if category.budget_program_id != budget.id or rule.budget_program_id != budget.id:
        raise ConfigurationError("Budget and category configuration do not match")

    actual_id, actual_reference = new_request_identity(submitted_at)
    evidence: list[dict[str, Any]] = []
    for definition in category.evidence_requirements:
        provided = command.evidence.get(definition.evidence_key)
        url = provided.url if provided else None
        note = provided.note if provided else None
        validate_https_url(url, f"evidence__{definition.evidence_key}")
        evidence.append(
            {
                "key": definition.evidence_key,
                "name_en": definition.name_en,
                "name_ko": definition.name_ko,
                "timing": definition.timing.value,
                "requirement": definition.requirement.value,
                "allow_waiver": definition.allow_waiver,
                "description_en": definition.description_en,
                "description_ko": definition.description_ko,
                "display_order": definition.display_order,
                "url": url,
                "note": note,
            }
        )

    validate_https_url(command.evidence_folder_url, "evidence_folder")
    payload = {
        "id": request_id or actual_id,
        "reference_number": reference_number or actual_reference,
        "applicant_slack_user_id": command.applicant_slack_user_id,
        "applicant_display_name": command.applicant_display_name,
        "applicant_type": command.applicant_type.value,
        "applicant_identifier": command.applicant_identifier,
        "department": {
            "id": department.id,
            "name_en": department.name_en,
            "name_ko": department.name_ko,
        },
        "budget": {
            "id": budget.id,
            "name_en": budget.name_en,
            "name_ko": budget.name_ko,
            "is_available": budget.is_available,
        },
        "category": {
            "id": category.id,
            "form_id": category.form_id,
            "form_name_en": category.form_name_en,
            "form_name_ko": category.form_name_ko,
            "name_en": category.name_en,
            "name_ko": category.name_ko,
            "budget_path_en": list(category.budget_path_en),
            "budget_path_ko": list(category.budget_path_ko),
        },
        "amount": str(command.amount),
        "currency": command.currency,
        "vendor": command.vendor,
        "payment_date": command.payment_date.isoformat(),
        "purpose": command.purpose,
        "evidence_folder_url": command.evidence_folder_url,
        "approval_channel_id": rule.approval_channel_id,
        "workflow": [
            {
                "step_order": index,
                "name_en": step.name_en,
                "name_ko": step.name_ko,
                "approver_slack_user_ids": list(step.approver_slack_user_ids),
                "approver_roles": list(step.approver_roles),
                "workflow_id": rule.workflow_id,
            }
            for index, step in enumerate(rule.steps, start=1)
        ],
        "evidence": evidence,
        "submitted_at": (submitted_at or utc_now()).isoformat(),
    }
    request = request_from_created(payload)
    validate_required_evidence(request.evidence_submissions, EvidenceTiming.PRE)
    return payload


def request_from_created(data: dict[str, Any]) -> ExpenseRequest:
    department_data = data["department"]
    budget_data = data["budget"]
    category_data = data["category"]
    department = Department(**department_data)
    budget = BudgetProgram(**budget_data)
    category = ExpenseCategory(
        id=category_data["id"],
        budget_program_id=budget.id,
        form_id=category_data.get("form_id", category_data["id"]),
        form_name_en=category_data.get("form_name_en", category_data["name_en"]),
        form_name_ko=category_data.get("form_name_ko", category_data["name_ko"]),
        name_en=category_data["name_en"],
        name_ko=category_data["name_ko"],
        budget_path_en=tuple(
            category_data.get("budget_path_en") or [budget.name_en, category_data["name_en"]]
        ),
        budget_path_ko=tuple(
            category_data.get("budget_path_ko") or [budget.name_ko, category_data["name_ko"]]
        ),
    )
    evidence = [
        EvidenceSubmission(
            requirement_key=item["key"],
            name_en=item["name_en"],
            name_ko=item["name_ko"],
            timing=EvidenceTiming(item["timing"]),
            requirement=EvidenceRequirementLevel(item["requirement"]),
            allow_waiver=bool(item.get("allow_waiver", False)),
            description_en=item.get("description_en"),
            description_ko=item.get("description_ko"),
            display_order=int(item["display_order"]),
            url=item.get("url"),
            note=item.get("note"),
            status=(
                EvidenceSubmissionStatus.SUBMITTED
                if item.get("url")
                else EvidenceSubmissionStatus.MISSING
            ),
            submitted_at=(
                datetime.fromisoformat(data["submitted_at"]) if item.get("url") else None
            ),
        )
        for item in data["evidence"]
    ]
    steps = [
        ApprovalStep(
            step_order=int(item["step_order"]),
            name_en=item["name_en"],
            name_ko=item["name_ko"],
            approvers=[
                ApprovalStepApprover(slack_user_id=value)
                for value in item["approver_slack_user_ids"]
            ],
            status=(
                ApprovalStepStatus.PENDING
                if int(item["step_order"]) == 1
                else ApprovalStepStatus.WAITING
            ),
        )
        for item in data["workflow"]
    ]
    if not steps or any(not step.approvers for step in steps):
        raise ConfigurationError("Approval workflow snapshot is incomplete")
    return ExpenseRequest(
        id=data["id"],
        reference_number=data["reference_number"],
        applicant_slack_user_id=data["applicant_slack_user_id"],
        applicant_display_name=data["applicant_display_name"],
        applicant_type=ApplicantType(data["applicant_type"]),
        applicant_identifier=data.get("applicant_identifier") or data.get("student_id"),
        department_id=department.id,
        budget_program_id=budget.id,
        category_id=category.id,
        amount=Decimal(data["amount"]),
        currency=data["currency"],
        vendor=data["vendor"],
        payment_date=datetime.fromisoformat(data["payment_date"]).date(),
        purpose=data["purpose"],
        evidence_folder_url=data.get("evidence_folder_url"),
        status=RequestStatus.IN_APPROVAL,
        current_step_order=1,
        approval_channel_id=data["approval_channel_id"],
        workflow_snapshot=copy.deepcopy(data["workflow"]),
        evidence_snapshot=copy.deepcopy(data["evidence"]),
        submitted_at=datetime.fromisoformat(data["submitted_at"]),
        department=department,
        budget_program=budget,
        category=category,
        evidence_submissions=evidence,
        approval_steps=steps,
    )


def current_step(request: ExpenseRequest) -> ApprovalStep:
    if request.status != RequestStatus.IN_APPROVAL or request.current_step_order is None:
        raise InvalidStateTransitionError("Request is not awaiting approval")
    return next(
        step for step in request.approval_steps if step.step_order == request.current_step_order
    )


def can_actor_approve(request: ExpenseRequest, actor: str) -> bool:
    try:
        step = current_step(request)
    except InvalidStateTransitionError:
        return False
    return actor in {item.slack_user_id for item in step.approvers}


def assert_actor_can_approve(request: ExpenseRequest, actor: str) -> ApprovalStep:
    step = current_step(request)
    if actor not in {item.slack_user_id for item in step.approvers}:
        raise ApprovalPermissionError("Actor is not assigned to the current step")
    return step


def editable_event_data(command: EditExpenseCommand) -> dict[str, Any]:
    return {
        "amount": str(command.amount),
        "vendor": command.vendor,
        "payment_date": command.payment_date.isoformat(),
        "purpose": command.purpose,
        "evidence_folder_url": command.evidence_folder_url,
        "evidence": {
            key: {"url": value.url, "note": value.note} for key, value in command.evidence.items()
        },
    }


def post_evidence_event_data(command: PostEvidenceCommand) -> dict[str, Any]:
    return {
        "evidence": {
            key: {"url": value.url, "note": value.note} for key, value in command.evidence.items()
        }
    }


def validate_transition(
    request: ExpenseRequest, kind: str, actor: str, data: dict[str, Any] | None = None
) -> None:
    candidate = copy.deepcopy(request)
    apply_event(candidate, kind, actor, data or {}, utc_now())


def apply_event(
    request: ExpenseRequest,
    kind: str,
    actor: str,
    data: dict[str, Any],
    event_time: datetime,
) -> None:
    if kind == APPROVAL_STEP_APPROVED:
        step = assert_actor_can_approve(request, actor)
        step.status = ApprovalStepStatus.APPROVED
        step.acted_by_slack_user_id = actor
        step.acted_at = event_time
        next_step = next(
            (item for item in request.approval_steps if item.step_order > step.step_order), None
        )
        if next_step is not None:
            next_step.status = ApprovalStepStatus.PENDING
            request.current_step_order = next_step.step_order
            return
        request.current_step_order = None
        request.status = (
            RequestStatus.COMPLETED
            if required_post_evidence_complete(request.evidence_submissions)
            else RequestStatus.APPROVED_PENDING_POST_EVIDENCE
        )
        return

    if kind == CHANGES_REQUESTED:
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise DomainValidationError("A reason is required")
        step = assert_actor_can_approve(request, actor)
        step.status = ApprovalStepStatus.CHANGES_REQUESTED
        step.acted_by_slack_user_id = actor
        step.comment = reason
        step.acted_at = event_time
        request.status = RequestStatus.CHANGES_REQUESTED
        return

    if kind == REQUEST_REJECTED:
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise DomainValidationError("A reason is required")
        step = assert_actor_can_approve(request, actor)
        step.status = ApprovalStepStatus.REJECTED
        step.acted_by_slack_user_id = actor
        step.comment = reason
        step.acted_at = event_time
        request.status = RequestStatus.REJECTED
        request.current_step_order = None
        return

    if kind == REQUEST_RESUBMITTED:
        if actor != request.applicant_slack_user_id:
            raise ApprovalPermissionError("Only the applicant can resubmit")
        if request.status != RequestStatus.CHANGES_REQUESTED:
            raise InvalidStateTransitionError("Request is not awaiting changes")
        validate_https_url(data.get("evidence_folder_url"), "evidence_folder")
        request.amount = Decimal(data["amount"])
        request.vendor = data["vendor"]
        request.payment_date = datetime.fromisoformat(data["payment_date"]).date()
        request.purpose = data["purpose"]
        request.evidence_folder_url = data.get("evidence_folder_url")
        for submission in request.evidence_submissions:
            if submission.timing != EvidenceTiming.PRE:
                continue
            value = data.get("evidence", {}).get(submission.requirement_key, {})
            validate_https_url(value.get("url"), f"evidence__{submission.requirement_key}")
            apply_evidence_value(submission, value.get("url"), value.get("note"))
        validate_required_evidence(request.evidence_submissions, EvidenceTiming.PRE)
        step = next(
            item for item in request.approval_steps if item.step_order == request.current_step_order
        )
        step.status = ApprovalStepStatus.PENDING
        step.acted_by_slack_user_id = None
        step.comment = None
        step.acted_at = None
        request.status = RequestStatus.IN_APPROVAL
        request.revision += 1
        return

    if kind == POST_EVIDENCE_SUBMITTED:
        if actor != request.applicant_slack_user_id:
            raise ApprovalPermissionError("Only the applicant can submit evidence")
        if request.status not in {
            RequestStatus.APPROVED_PENDING_POST_EVIDENCE,
            RequestStatus.COMPLETED,
        }:
            raise InvalidStateTransitionError("Post evidence cannot be submitted yet")
        for submission in request.evidence_submissions:
            if submission.timing != EvidenceTiming.POST:
                continue
            value = data.get("evidence", {}).get(submission.requirement_key, {})
            if not value:
                continue
            validate_https_url(value.get("url"), f"evidence__{submission.requirement_key}")
            apply_evidence_value(submission, value.get("url"), value.get("note"))
        validate_required_evidence(request.evidence_submissions, EvidenceTiming.POST)
        if required_post_evidence_complete(request.evidence_submissions):
            request.status = RequestStatus.COMPLETED
        return

    raise InvalidStateTransitionError(f"Unsupported event: {kind}")


def replay_events(events: list[dict[str, Any]], *, message_ts: str) -> ExpenseRequest:
    ordered = sorted(events, key=lambda item: item["ts"])
    created = next((item for item in ordered if item["kind"] == REQUEST_CREATED), None)
    if created is None:
        raise ConfigurationError("Expense record has no creation event")
    request = request_from_created(created["data"])
    request.message_ts = message_ts
    for event in ordered:
        if event is created or event["kind"] == REQUEST_CREATED:
            continue
        try:
            apply_event(
                request,
                event["kind"],
                event.get("actor", ""),
                event.get("data", {}),
                datetime.fromisoformat(event["at"]),
            )
        except DomainError:
            # Concurrent or stale actions stay in the immutable audit thread but do not alter state.
            continue
    return request


def request_summary(request: ExpenseRequest) -> dict[str, Any]:
    approvers: list[str] = []
    if request.status == RequestStatus.IN_APPROVAL:
        approvers = [item.slack_user_id for item in current_step(request).approvers]
    return {
        "version": 1,
        "request_id": request.id,
        "reference_number": request.reference_number,
        "applicant_slack_user_id": request.applicant_slack_user_id,
        "department_id": request.department_id,
        "category_id": request.category_id,
        "status": request.status.value,
        "current_approver_slack_user_ids": approvers,
        "approval_channel_id": request.approval_channel_id,
        "revision": request.revision,
    }
