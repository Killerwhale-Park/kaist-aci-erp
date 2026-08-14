from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.work_lifecycle import PendingWorkAction, lifecycle_adapter_for
from app.domain.enums import RequestStatus, WorkRequestKind, WorkRequestStatus
from app.domain.models import ExpenseRequest, WorkRequest


class WorkItemSource(StrEnum):
    EXPENSE = "EXPENSE"
    WORK_REQUEST = "WORK_REQUEST"


class WorkItemAction(StrEnum):
    VIEW_EXPENSE = "VIEW_EXPENSE"
    VIEW_WORK = "VIEW_WORK"
    EDIT_EXPENSE = "EDIT_EXPENSE"
    SUBMIT_POST_EVIDENCE = "SUBMIT_POST_EVIDENCE"
    APPROVE_EXPENSE = "APPROVE_EXPENSE"
    REQUEST_EXPENSE_CHANGES = "REQUEST_EXPENSE_CHANGES"
    REJECT_EXPENSE = "REJECT_EXPENSE"
    APPROVE_WORK = "APPROVE_WORK"
    REJECT_WORK = "REJECT_WORK"
    HANDOFF_PURCHASE = "HANDOFF_PURCHASE"
    START_SETTLEMENT = "START_SETTLEMENT"
    COMPLETE_WORK = "COMPLETE_WORK"


@dataclass(frozen=True)
class WorkItem:
    source: WorkItemSource
    source_id: str
    reference_number: str
    title_en: str
    title_ko: str
    status: str
    occurred_at: datetime
    actions: tuple[WorkItemAction, ...]


@dataclass(frozen=True)
class UserWorkQueue:
    submitted: tuple[WorkItem, ...]
    action_required: tuple[WorkItem, ...]


class WorkItemAdapter(Protocol):
    def submitted(self, slack_user_id: str) -> list[WorkItem]: ...

    def action_required(self, slack_user_id: str) -> list[WorkItem]: ...


class ExpenseWorkItemAdapter:
    def __init__(
        self,
        own_requests: list[ExpenseRequest],
        pending_approvals: list[ExpenseRequest],
    ) -> None:
        self.own_requests = own_requests
        self.pending_approvals = pending_approvals

    @staticmethod
    def _item(request: ExpenseRequest, actions: tuple[WorkItemAction, ...]) -> WorkItem:
        return WorkItem(
            source=WorkItemSource.EXPENSE,
            source_id=request.id,
            reference_number=request.reference_number,
            title_en=request.category.name_en,
            title_ko=request.category.name_ko,
            status=request.status.value,
            occurred_at=request.submitted_at,
            actions=actions,
        )

    def submitted(self, slack_user_id: str) -> list[WorkItem]:
        return [
            self._item(request, (WorkItemAction.VIEW_EXPENSE,))
            for request in self.own_requests
            if request.applicant_slack_user_id == slack_user_id
        ]

    def action_required(self, slack_user_id: str) -> list[WorkItem]:
        items = [
            self._item(
                request,
                (
                    WorkItemAction.VIEW_EXPENSE,
                    WorkItemAction.APPROVE_EXPENSE,
                    WorkItemAction.REQUEST_EXPENSE_CHANGES,
                    WorkItemAction.REJECT_EXPENSE,
                ),
            )
            for request in self.pending_approvals
            if slack_user_id in request.current_approver_slack_user_ids
        ]
        for request in self.own_requests:
            if request.applicant_slack_user_id != slack_user_id:
                continue
            if request.status == RequestStatus.CHANGES_REQUESTED:
                items.append(self._item(request, (WorkItemAction.EDIT_EXPENSE,)))
            elif request.status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE:
                items.append(self._item(request, (WorkItemAction.SUBMIT_POST_EVIDENCE,)))
        return items


_LIFECYCLE_ACTIONS = {
    PendingWorkAction.HANDOFF_SETTLEMENT: WorkItemAction.HANDOFF_PURCHASE,
    PendingWorkAction.START_EXPENSE: WorkItemAction.START_SETTLEMENT,
    PendingWorkAction.COMPLETE: WorkItemAction.COMPLETE_WORK,
}


class WorkRequestItemAdapter:
    def __init__(
        self,
        submitted_requests: list[WorkRequest],
        actionable_requests: list[WorkRequest],
    ) -> None:
        self.submitted_requests = submitted_requests
        self.actionable_requests = actionable_requests

    @staticmethod
    def _item(request: WorkRequest, actions: tuple[WorkItemAction, ...]) -> WorkItem:
        kind = (
            "Purchase Request" if request.kind == WorkRequestKind.PURCHASE else "Settlement Request"
        )
        kind_ko = "구매 요청" if request.kind == WorkRequestKind.PURCHASE else "정산 요청"
        return WorkItem(
            source=WorkItemSource.WORK_REQUEST,
            source_id=request.id,
            reference_number=request.reference_number,
            title_en=f"{kind}: {request.subject}",
            title_ko=f"{kind_ko}: {request.subject}",
            status=request.status.value,
            occurred_at=request.created_at,
            actions=actions,
        )

    def submitted(self, slack_user_id: str) -> list[WorkItem]:
        return [
            self._item(request, (WorkItemAction.VIEW_WORK,))
            for request in self.submitted_requests
            if slack_user_id
            in {
                request.requester_slack_user_id,
                request.originator_slack_user_id,
            }
        ]

    def action_required(self, slack_user_id: str) -> list[WorkItem]:
        items: list[WorkItem] = []
        for request in self.actionable_requests:
            if request.status == WorkRequestStatus.IN_APPROVAL:
                if slack_user_id not in request.current_approver_slack_user_ids:
                    continue
                items.append(
                    self._item(
                        request,
                        (
                            WorkItemAction.VIEW_WORK,
                            WorkItemAction.APPROVE_WORK,
                            WorkItemAction.REJECT_WORK,
                        ),
                    )
                )
                continue
            if request.assignee_slack_user_id != slack_user_id:
                continue
            adapter = lifecycle_adapter_for(request)
            items.append(
                self._item(
                    request,
                    (
                        WorkItemAction.VIEW_WORK,
                        _LIFECYCLE_ACTIONS[adapter.pending_action],
                    ),
                )
            )
        return items


def build_user_work_queue(
    slack_user_id: str,
    *,
    own_expenses: list[ExpenseRequest],
    pending_expense_approvals: list[ExpenseRequest],
    submitted_work_requests: list[WorkRequest],
    actionable_work_requests: list[WorkRequest],
) -> UserWorkQueue:
    adapters: tuple[WorkItemAdapter, ...] = (
        ExpenseWorkItemAdapter(own_expenses, pending_expense_approvals),
        WorkRequestItemAdapter(submitted_work_requests, actionable_work_requests),
    )
    submitted = [item for adapter in adapters for item in adapter.submitted(slack_user_id)]
    action_required = [
        item for adapter in adapters for item in adapter.action_required(slack_user_id)
    ]
    submitted.sort(key=lambda item: item.occurred_at, reverse=True)
    action_required.sort(key=lambda item: item.occurred_at, reverse=True)
    return UserWorkQueue(tuple(submitted), tuple(action_required))
