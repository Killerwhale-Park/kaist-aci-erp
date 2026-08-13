from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.catalog import department_by_id
from app.domain.enums import WorkRequestKind, WorkRequestStatus
from app.domain.models import Department, WorkRequest
from app.exceptions import ConfigurationError, InvalidStateTransitionError
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand

WORK_REQUEST_CREATED = "WORK_REQUEST_CREATED"
WORK_REQUEST_COMPLETED = "WORK_REQUEST_COMPLETED"


def _identity(kind: WorkRequestKind, now: datetime) -> tuple[str, str]:
    request_id = str(uuid4())
    prefix = "BUY" if kind == WorkRequestKind.PURCHASE else "SET"
    reference = f"{prefix}-{now:%Y%m%d}-{request_id[:6].upper()}"
    return request_id, reference


def purchase_created_data(
    command: CreatePurchaseRequestCommand, department: Department
) -> dict[str, Any]:
    now = datetime.now(UTC)
    request_id, reference = _identity(WorkRequestKind.PURCHASE, now)
    return {
        "id": request_id,
        "reference_number": reference,
        "kind": WorkRequestKind.PURCHASE.value,
        "requester_slack_user_id": command.requester_slack_user_id,
        "assignee_slack_user_id": command.assignee_slack_user_id,
        "department_id": department.id,
        "channel_id": command.channel_id,
        "subject": command.item_name,
        "purpose": command.purpose,
        "quantity": command.quantity,
        "amount": str(command.estimated_amount) if command.estimated_amount is not None else None,
        "source_url": command.product_url,
        "vendor": None,
        "payment_date": None,
        "evidence_folder_url": None,
        "created_at": now.isoformat(),
    }


def settlement_created_data(
    command: CreateSettlementRequestCommand, department: Department
) -> dict[str, Any]:
    now = datetime.now(UTC)
    request_id, reference = _identity(WorkRequestKind.SETTLEMENT, now)
    return {
        "id": request_id,
        "reference_number": reference,
        "kind": WorkRequestKind.SETTLEMENT.value,
        "requester_slack_user_id": command.requester_slack_user_id,
        "assignee_slack_user_id": command.assignee_slack_user_id,
        "department_id": department.id,
        "channel_id": command.channel_id,
        "subject": command.subject,
        "purpose": command.purpose,
        "quantity": None,
        "amount": str(command.amount),
        "source_url": None,
        "vendor": command.vendor,
        "payment_date": command.payment_date.isoformat(),
        "evidence_folder_url": command.evidence_folder_url,
        "created_at": now.isoformat(),
    }


def work_request_from_created(data: dict[str, Any]) -> WorkRequest:
    department = department_by_id(data["department_id"])
    if department is None:
        raise ConfigurationError("Work request department is unavailable")
    return WorkRequest(
        id=data["id"],
        reference_number=data["reference_number"],
        kind=WorkRequestKind(data["kind"]),
        requester_slack_user_id=data["requester_slack_user_id"],
        assignee_slack_user_id=data["assignee_slack_user_id"],
        department_id=department.id,
        channel_id=data["channel_id"],
        subject=data["subject"],
        purpose=data["purpose"],
        department=department,
        created_at=datetime.fromisoformat(data["created_at"]),
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
    )


def apply_work_event(request: WorkRequest, kind: str, actor: str, event_time: datetime) -> None:
    if kind != WORK_REQUEST_COMPLETED:
        raise InvalidStateTransitionError(f"Unsupported work request event: {kind}")
    if request.status != WorkRequestStatus.OPEN:
        raise InvalidStateTransitionError("Work request is already completed")
    request.status = WorkRequestStatus.COMPLETED
    request.completed_by_slack_user_id = actor
    request.completed_at = event_time


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
            )
        except InvalidStateTransitionError:
            continue
    return request


def work_request_summary(request: WorkRequest) -> dict[str, Any]:
    return {
        "version": 1,
        "work_request_id": request.id,
        "reference_number": request.reference_number,
        "kind": request.kind.value,
        "requester_slack_user_id": request.requester_slack_user_id,
        "assignee_slack_user_id": request.assignee_slack_user_id,
        "department_id": request.department_id,
        "status": request.status.value,
        "channel_id": request.channel_id,
    }
