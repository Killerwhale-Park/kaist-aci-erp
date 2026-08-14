from __future__ import annotations

from datetime import datetime

from app.domain.enums import ApprovalStepStatus
from app.domain.models import ApprovalStep
from app.exceptions import ApprovalPermissionError, InvalidStateTransitionError


def pending_step(steps: list[ApprovalStep], current_order: int | None) -> ApprovalStep:
    if current_order is None:
        raise InvalidStateTransitionError("Approval chain has no pending step")
    step = next((item for item in steps if item.step_order == current_order), None)
    if step is None or step.status != ApprovalStepStatus.PENDING:
        raise InvalidStateTransitionError("Approval chain projection is inconsistent")
    return step


def assert_actor_can_approve_step(
    steps: list[ApprovalStep], current_order: int | None, actor: str
) -> ApprovalStep:
    step = pending_step(steps, current_order)
    if actor not in {item.slack_user_id for item in step.approvers}:
        raise ApprovalPermissionError("Actor is not assigned to the current step")
    return step


def approve_step(
    steps: list[ApprovalStep], current_order: int | None, actor: str, acted_at: datetime
) -> int | None:
    step = assert_actor_can_approve_step(steps, current_order, actor)
    step.status = ApprovalStepStatus.APPROVED
    step.acted_by_slack_user_id = actor
    step.acted_at = acted_at
    following = next(
        (item for item in steps if item.step_order > step.step_order),
        None,
    )
    if following is None:
        return None
    following.status = ApprovalStepStatus.PENDING
    return following.step_order
