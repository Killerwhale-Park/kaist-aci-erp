from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from app.config.work_request_policies import work_request_policy
from app.domain.models import WorkRequest
from app.exceptions import ApprovalPermissionError, InvalidStateTransitionError


class PendingWorkAction(StrEnum):
    HANDOFF_SETTLEMENT = "HANDOFF_SETTLEMENT"
    START_EXPENSE = "START_EXPENSE"
    COMPLETE = "COMPLETE"


class WorkRequestLifecycleAdapter(Protocol):
    pending_action: PendingWorkAction

    def assert_completion(
        self,
        request: WorkRequest,
        actor: str,
        successor_type: str | None,
        successor_id: str | None,
    ) -> None: ...


def _assert_assignee(request: WorkRequest, actor: str) -> None:
    if actor != request.assignee_slack_user_id:
        raise ApprovalPermissionError("Only the current assignee can complete this task")


class PurchaseToSettlementAdapter:
    pending_action = PendingWorkAction.HANDOFF_SETTLEMENT

    def assert_completion(
        self,
        request: WorkRequest,
        actor: str,
        successor_type: str | None,
        successor_id: str | None,
    ) -> None:
        _assert_assignee(request, actor)
        if successor_type != "SETTLEMENT_WORK_REQUEST" or not successor_id:
            raise InvalidStateTransitionError(
                "Purchase completion requires an atomic settlement handoff"
            )


class SettlementToExpenseAdapter:
    pending_action = PendingWorkAction.START_EXPENSE

    def assert_completion(
        self,
        request: WorkRequest,
        actor: str,
        successor_type: str | None,
        successor_id: str | None,
    ) -> None:
        _assert_assignee(request, actor)
        if successor_type != "EXPENSE_REQUEST" or not successor_id:
            raise InvalidStateTransitionError(
                "Settlement completion requires a resulting expense request"
            )


class CloseOnCompletionAdapter:
    pending_action = PendingWorkAction.COMPLETE

    def assert_completion(
        self,
        request: WorkRequest,
        actor: str,
        successor_type: str | None,
        successor_id: str | None,
    ) -> None:
        _assert_assignee(request, actor)
        if successor_type or successor_id:
            raise InvalidStateTransitionError("Terminal work cannot create a successor")


_ADAPTERS: dict[str, WorkRequestLifecycleAdapter] = {
    "purchase_to_settlement": PurchaseToSettlementAdapter(),
    "settlement_to_expense": SettlementToExpenseAdapter(),
    "close_on_completion": CloseOnCompletionAdapter(),
}


def lifecycle_adapter_for(request: WorkRequest) -> WorkRequestLifecycleAdapter:
    policy = work_request_policy(request.kind)
    return _ADAPTERS[policy.lifecycle_adapter]
