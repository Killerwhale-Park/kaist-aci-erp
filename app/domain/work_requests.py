from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.approval_chain import approve_step, assert_actor_can_approve_step, pending_step
from app.domain.catalog import budget_path, department_by_id
from app.domain.enums import ApprovalStepStatus, WorkRequestKind, WorkRequestStatus
from app.domain.models import (
    ApprovalStep,
    ApprovalStepApprover,
    Department,
    ExpenseCategory,
    ResolvedApprovalWorkflow,
    WorkRequest,
)
from app.exceptions import (
    ConfigurationError,
    DomainError,
    InvalidStateTransitionError,
)
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand

WORK_REQUEST_CREATED = "WORK_REQUEST_CREATED"
WORK_APPROVAL_STEP_APPROVED = "WORK_APPROVAL_STEP_APPROVED"
WORK_REQUEST_REJECTED = "WORK_REQUEST_REJECTED"
WORK_REQUEST_COMPLETED = "WORK_REQUEST_COMPLETED"


def _identity(kind: WorkRequestKind, now: datetime) -> tuple[str, str]:
    request_id = str(uuid4())
    prefix = "BUY" if kind == WorkRequestKind.PURCHASE else "SET"
    reference = f"{prefix}-{now:%Y%m%d}-{request_id[:6].upper()}"
    return request_id, reference


def purchase_created_data(
    command: CreatePurchaseRequestCommand,
    department: Department,
    approval_workflow: ResolvedApprovalWorkflow,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    request_id, reference = _identity(WorkRequestKind.PURCHASE, now)
    return {
        "id": request_id,
        "reference_number": reference,
        "kind": WorkRequestKind.PURCHASE.value,
        "requester_slack_user_id": command.requester_slack_user_id,
        "originator_slack_user_id": command.requester_slack_user_id,
        "assignee_slack_user_id": command.assignee_slack_user_id,
        "case_id": request_id,
        "parent_request_id": None,
        "department_id": department.id,
        "channel_id": command.channel_id,
        "source_conversation_id": command.source_conversation_id,
        "subject": command.item_name,
        "purpose": command.purpose,
        "quantity": command.quantity,
        "amount": str(command.estimated_amount) if command.estimated_amount is not None else None,
        "source_url": command.product_url,
        "vendor": None,
        "payment_date": None,
        "evidence_folder_url": None,
        "workflow": _workflow_snapshot(approval_workflow),
        "created_at": now.isoformat(),
    }


def settlement_created_data(
    command: CreateSettlementRequestCommand,
    department: Department,
    category: ExpenseCategory,
    delivery_channel_id: str,
    *,
    originator_slack_user_id: str | None = None,
    case_id: str | None = None,
    parent_request_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    request_id, reference = _identity(WorkRequestKind.SETTLEMENT, now)
    selected_path = budget_path(command.budget_node_id)
    return {
        "id": request_id,
        "reference_number": reference,
        "kind": WorkRequestKind.SETTLEMENT.value,
        "requester_slack_user_id": command.requester_slack_user_id,
        "originator_slack_user_id": (originator_slack_user_id or command.requester_slack_user_id),
        "assignee_slack_user_id": command.assignee_slack_user_id,
        "case_id": case_id or request_id,
        "parent_request_id": parent_request_id,
        "department_id": department.id,
        "channel_id": delivery_channel_id,
        "source_conversation_id": command.source_conversation_id,
        "budget_selection": {
            "budget_program_id": category.budget_program_id,
            "budget_node_id": command.budget_node_id,
            "budget_node_path": [item.id for item in selected_path],
            "budget_path_en": list(category.budget_path_en),
            "budget_path_ko": list(category.budget_path_ko),
        },
        "subject": command.subject,
        "purpose": command.purpose,
        "quantity": None,
        "amount": str(command.amount),
        "source_url": None,
        "vendor": command.vendor,
        "payment_date": command.payment_date.isoformat(),
        "evidence_folder_url": command.evidence_folder_url,
        "workflow": [],
        "created_at": now.isoformat(),
    }


def _workflow_snapshot(workflow: ResolvedApprovalWorkflow) -> list[dict[str, Any]]:
    if not workflow.is_complete:
        raise ConfigurationError("Work request approval workflow is incomplete")
    return [
        {
            "step_order": order,
            "name_en": step.name_en,
            "name_ko": step.name_ko,
            "approver_slack_user_ids": list(step.approver_slack_user_ids),
            "approver_roles": list(step.approver_roles),
            "workflow_id": workflow.id,
        }
        for order, step in enumerate(workflow.steps, start=1)
    ]


def work_request_from_created(data: dict[str, Any]) -> WorkRequest:
    department = department_by_id(data["department_id"])
    if department is None:
        raise ConfigurationError("Work request department is unavailable")
    workflow = copy.deepcopy(data.get("workflow") or [])
    budget_selection = copy.deepcopy(data.get("budget_selection") or {})
    steps = [
        ApprovalStep(
            step_order=int(item["step_order"]),
            name_en=item["name_en"],
            name_ko=item["name_ko"],
            approvers=[
                ApprovalStepApprover(slack_user_id=user_id)
                for user_id in item["approver_slack_user_ids"]
            ],
            status=(
                ApprovalStepStatus.PENDING
                if int(item["step_order"]) == 1
                else ApprovalStepStatus.WAITING
            ),
        )
        for item in workflow
    ]
    return WorkRequest(
        id=data["id"],
        reference_number=data["reference_number"],
        kind=WorkRequestKind(data["kind"]),
        requester_slack_user_id=data["requester_slack_user_id"],
        originator_slack_user_id=data.get(
            "originator_slack_user_id", data["requester_slack_user_id"]
        ),
        assignee_slack_user_id=data["assignee_slack_user_id"],
        case_id=data.get("case_id", data["id"]),
        parent_request_id=data.get("parent_request_id"),
        department_id=department.id,
        channel_id=data["channel_id"],
        source_conversation_id=data.get("source_conversation_id"),
        subject=data["subject"],
        purpose=data["purpose"],
        department=department,
        created_at=datetime.fromisoformat(data["created_at"]),
        workflow_snapshot=workflow,
        approval_steps=steps,
        current_step_order=1 if steps else None,
        budget_program_id=budget_selection.get("budget_program_id"),
        budget_node_id=budget_selection.get("budget_node_id"),
        budget_node_path=tuple(budget_selection.get("budget_node_path") or ()),
        budget_path_en=tuple(budget_selection.get("budget_path_en") or ()),
        budget_path_ko=tuple(budget_selection.get("budget_path_ko") or ()),
        quantity=int(data["quantity"]) if data.get("quantity") is not None else None,
        amount=Decimal(data["amount"]) if data.get("amount") is not None else None,
        vendor=data.get("vendor"),
        payment_date=(
            datetime.fromisoformat(data["payment_date"]).date()
            if data.get("payment_date")
            else None
        ),
        source_url=data.get("source_url"),
        evidence_folder_url=data.get("evidence_folder_url"),
        status=(WorkRequestStatus.IN_APPROVAL if steps else WorkRequestStatus.ACTION_REQUIRED),
    )


def current_work_approval_step(request: WorkRequest) -> ApprovalStep:
    if request.status != WorkRequestStatus.IN_APPROVAL:
        raise InvalidStateTransitionError("Work request is not awaiting approval")
    return pending_step(request.approval_steps, request.current_step_order)


def assert_actor_can_approve_work(request: WorkRequest, actor: str) -> ApprovalStep:
    if request.status != WorkRequestStatus.IN_APPROVAL:
        raise InvalidStateTransitionError("Work request is not awaiting approval")
    return assert_actor_can_approve_step(request.approval_steps, request.current_step_order, actor)


def apply_work_event(
    request: WorkRequest,
    kind: str,
    actor: str,
    event_time: datetime,
    data: dict[str, Any] | None = None,
) -> None:
    if kind == WORK_APPROVAL_STEP_APPROVED:
        if request.status != WorkRequestStatus.IN_APPROVAL:
            raise InvalidStateTransitionError("Work request is not awaiting approval")
        request.current_step_order = approve_step(
            request.approval_steps,
            request.current_step_order,
            actor,
            event_time,
        )
        if request.current_step_order is None:
            request.status = WorkRequestStatus.ACTION_REQUIRED
        return
    if kind == WORK_REQUEST_REJECTED:
        assert_actor_can_approve_work(request, actor)
        reason = str((data or {}).get("reason") or "").strip()
        if not reason:
            raise InvalidStateTransitionError("Work request rejection requires a reason")
        request.current_step_order = None
        request.status = WorkRequestStatus.REJECTED
        request.rejection_reason = reason
        return
    if kind == WORK_REQUEST_COMPLETED:
        if request.status not in {
            WorkRequestStatus.ACTION_REQUIRED,
            WorkRequestStatus.OPEN,
        }:
            raise InvalidStateTransitionError("Work request is not ready to complete")
        request.status = WorkRequestStatus.COMPLETED
        request.completed_by_slack_user_id = actor
        request.completed_at = event_time
        request.successor_type = (data or {}).get("successor_type")
        request.successor_id = (data or {}).get("successor_id")
        return
    raise InvalidStateTransitionError(f"Unsupported work request event: {kind}")


def replay_work_events(events: list[dict[str, Any]], *, message_ts: str | None) -> WorkRequest:
    ordered = sorted(events, key=lambda item: item["ts"])
    created = next((item for item in ordered if item["kind"] == WORK_REQUEST_CREATED), None)
    if created is None:
        raise ConfigurationError("Work request has no creation event")
    request = work_request_from_created(copy.deepcopy(created["data"]))
    request.message_ts = message_ts
    for event in ordered:
        if event is created or event["kind"] == WORK_REQUEST_CREATED:
            continue
        try:
            apply_work_event(
                request,
                event["kind"],
                event.get("actor", ""),
                datetime.fromisoformat(event["at"]),
                event.get("data", {}),
            )
        except DomainError:
            continue
    return request


def work_request_summary(request: WorkRequest) -> dict[str, Any]:
    return {
        "version": 2,
        "work_request_id": request.id,
        "reference_number": request.reference_number,
        "kind": request.kind.value,
        "requester_slack_user_id": request.requester_slack_user_id,
        "originator_slack_user_id": request.originator_slack_user_id,
        "assignee_slack_user_id": request.assignee_slack_user_id,
        "case_id": request.case_id,
        "parent_request_id": request.parent_request_id,
        "department_id": request.department_id,
        "source_conversation_id": request.source_conversation_id,
        "budget_program_id": request.budget_program_id,
        "budget_node_id": request.budget_node_id,
        "status": request.status.value,
        "current_step_order": request.current_step_order,
        "current_approver_slack_user_ids": list(request.current_approver_slack_user_ids),
        "successor_type": request.successor_type,
        "successor_id": request.successor_id,
        "channel_id": request.channel_id,
    }
