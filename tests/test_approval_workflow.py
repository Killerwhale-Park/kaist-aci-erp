from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.catalog import budget_by_id, category_by_id, department_by_id
from app.domain.enums import ApprovalStepStatus, RequestStatus
from app.domain.models import ApprovalRule, ApprovalRuleStep
from app.domain.workflow import (
    APPROVAL_STEP_APPROVED,
    CHANGES_REQUESTED,
    POST_EVIDENCE_SUBMITTED,
    REQUEST_CREATED,
    REQUEST_REJECTED,
    REQUEST_RESUBMITTED,
    apply_event,
    created_event_data,
    editable_event_data,
    post_evidence_event_data,
    replay_events,
    request_from_created,
    utc_now,
)
from app.exceptions import ApprovalPermissionError
from app.expenses.schemas import (
    CreateExpenseCommand,
    EditExpenseCommand,
    EvidenceInput,
    PostEvidenceCommand,
)


def make_rule(step_count: int, *, approvers: dict[int, tuple[str, ...]] | None = None):
    return ApprovalRule(
        department_id="department_1",
        budget_program_id="department_budget",
        category_id="supplies",
        approval_channel_id="C_APPROVAL",
        steps=tuple(
            ApprovalRuleStep(
                name_en=f"Step {index}",
                name_ko=f"단계 {index}",
                approver_slack_user_ids=(approvers or {}).get(index, (f"U_APPROVER_{index}",)),
            )
            for index in range(1, step_count + 1)
        ),
        version=1,
    )


def make_created(
    step_count: int = 2,
    *,
    approvers: dict[int, tuple[str, ...]] | None = None,
    evidence: dict[str, EvidenceInput] | None = None,
):
    command = CreateExpenseCommand(
        applicant_slack_user_id="U_STUDENT",
        applicant_display_name="Student",
        applicant_type="STUDENT",
        applicant_identifier="20260001",
        department_id="department_1",
        budget_program_id="department_budget",
        category_id="supplies",
        amount=Decimal("120000"),
        vendor="Airline",
        payment_date=date(2026, 8, 13),
        purpose="Conference travel",
        evidence_folder_url="https://drive.google.com/drive/folders/example",
        evidence=evidence or {},
    )
    return created_event_data(
        command,
        make_rule(step_count, approvers=approvers),
        department=department_by_id("department_1"),
        budget=budget_by_id("department_budget"),
        category=category_by_id("supplies"),
        request_id="REQ-1",
        reference_number="EXP-TEST-1",
    )


@pytest.mark.parametrize("step_count", [1, 2, 3])
def test_generic_n_step_approval(step_count: int) -> None:
    request = request_from_created(make_created(step_count))
    for index in range(1, step_count + 1):
        apply_event(request, APPROVAL_STEP_APPROVED, f"U_APPROVER_{index}", {}, utc_now())
    assert request.status == RequestStatus.COMPLETED
    assert request.current_step_order is None
    assert {step.status for step in request.approval_steps} == {ApprovalStepStatus.APPROVED}


def test_any_assigned_approver_can_approve_and_others_cannot() -> None:
    request = request_from_created(make_created(1, approvers={1: ("U_APPROVER_A", "U_APPROVER_B")}))
    with pytest.raises(ApprovalPermissionError):
        apply_event(request, APPROVAL_STEP_APPROVED, "U_OTHER", {}, utc_now())
    apply_event(request, APPROVAL_STEP_APPROVED, "U_APPROVER_B", {}, utc_now())
    assert request.status == RequestStatus.COMPLETED


def test_reject_and_changes_requested_transitions() -> None:
    rejected = request_from_created(make_created(2))
    apply_event(rejected, REQUEST_REJECTED, "U_APPROVER_1", {"reason": "No"}, utc_now())
    assert rejected.status == RequestStatus.REJECTED

    changed = request_from_created(make_created(2))
    apply_event(changed, APPROVAL_STEP_APPROVED, "U_APPROVER_1", {}, utc_now())
    apply_event(
        changed,
        CHANGES_REQUESTED,
        "U_APPROVER_2",
        {"reason": "Clear receipt"},
        utc_now(),
    )
    command = EditExpenseCommand(
        amount=Decimal("121000"),
        vendor="Airline",
        payment_date=date(2026, 8, 13),
        purpose="Conference travel, clarified",
        evidence_folder_url="https://drive.google.com/drive/folders/example",
        evidence={},
    )
    apply_event(
        changed,
        REQUEST_RESUBMITTED,
        "U_STUDENT",
        editable_event_data(command),
        utc_now(),
    )
    assert changed.status == RequestStatus.IN_APPROVAL
    assert changed.current_step_order == 2
    assert changed.approval_steps[0].status == ApprovalStepStatus.APPROVED


def test_required_post_evidence_waits_for_submission() -> None:
    created = make_created(1)
    for item in created["evidence"]:
        if item["key"] == "item_photo":
            item["requirement"] = "REQUIRED"
            item["timing"] = "POST"
    request = request_from_created(created)
    apply_event(request, APPROVAL_STEP_APPROVED, "U_APPROVER_1", {}, utc_now())
    assert request.status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE
    command = PostEvidenceCommand(
        evidence={"item_photo": EvidenceInput(url="https://drive.google.com/file/d/item-photo")}
    )
    apply_event(
        request,
        POST_EVIDENCE_SUBMITTED,
        "U_STUDENT",
        post_evidence_event_data(command),
        utc_now(),
    )
    assert request.status == RequestStatus.COMPLETED


def test_replay_ignores_stale_concurrent_action() -> None:
    now = utc_now().isoformat()
    events = [
        {
            "ts": "1",
            "kind": REQUEST_CREATED,
            "actor": "U_STUDENT",
            "at": now,
            "data": make_created(1),
        },
        {"ts": "2", "kind": APPROVAL_STEP_APPROVED, "actor": "U_APPROVER_1", "at": now, "data": {}},
        {
            "ts": "3",
            "kind": REQUEST_REJECTED,
            "actor": "U_APPROVER_1",
            "at": now,
            "data": {"reason": "late"},
        },
    ]
    request = replay_events(events, message_ts="ROOT")
    assert request.status == RequestStatus.COMPLETED
    assert request.message_ts == "ROOT"


def test_workflow_snapshot_is_immutable() -> None:
    created = make_created(2)
    request = request_from_created(created)
    created["workflow"].append(
        {
            "step_order": 3,
            "name_en": "Later configuration",
            "name_ko": "나중 설정",
            "approver_slack_user_ids": ["U_LATER"],
        }
    )
    assert len(request.workflow_snapshot) == 2


def test_legacy_student_id_snapshot_is_still_readable() -> None:
    created = make_created(1)
    created["student_id"] = created.pop("applicant_identifier")
    request = request_from_created(created)
    assert request.applicant_identifier == "20260001"


def test_professor_requires_employee_identifier() -> None:
    values = {
        "applicant_slack_user_id": "U_PROFESSOR",
        "applicant_display_name": "Professor",
        "applicant_type": "PROFESSOR",
        "department_id": "department_1",
        "budget_program_id": "department_budget",
        "category_id": "supplies",
        "amount": "10000",
        "vendor": "Vendor",
        "payment_date": date(2026, 8, 13),
        "purpose": "Lab supplies",
    }
    with pytest.raises(ValidationError):
        CreateExpenseCommand(**values)

    command = CreateExpenseCommand(**values, applicant_identifier="E12345")
    assert command.applicant_identifier == "E12345"
