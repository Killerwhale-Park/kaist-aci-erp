from app.application.work_items import WorkItemAction, build_user_work_queue
from app.domain.catalog import department_by_id
from app.domain.work_requests import (
    WORK_APPROVAL_STEP_APPROVED,
    apply_work_event,
    purchase_created_data,
    settlement_created_data,
    work_request_from_created,
)
from app.domain.workflow import request_from_created
from app.slack.home import app_home_view
from tests.test_approval_workflow import make_created
from tests.test_work_requests import purchase_command, purchase_workflow, settlement_command


def test_work_queue_uses_relationships_instead_of_roles() -> None:
    department = department_by_id("department_1")
    expense = request_from_created(make_created(1))
    purchase = work_request_from_created(
        purchase_created_data(
            purchase_command(),
            department,
            purchase_workflow("U_PROF"),
        )
    )

    student_queue = build_user_work_queue(
        "U_STUDENT",
        own_expenses=[expense],
        pending_expense_approvals=[],
        submitted_work_requests=[purchase],
        actionable_work_requests=[],
    )
    assert {item.source_id for item in student_queue.submitted} == {
        expense.id,
        purchase.id,
    }
    assert student_queue.action_required == ()

    professor_queue = build_user_work_queue(
        "U_PROF",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[],
        actionable_work_requests=[purchase],
    )
    assert professor_queue.action_required[0].actions == (
        WorkItemAction.VIEW_WORK,
        WorkItemAction.APPROVE_WORK,
        WorkItemAction.REJECT_WORK,
    )

    apply_work_event(
        purchase,
        WORK_APPROVAL_STEP_APPROVED,
        "U_PROF",
        purchase.created_at,
    )
    payment_queue = build_user_work_queue(
        "U_PROF",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[],
        actionable_work_requests=[purchase],
    )
    assert WorkItemAction.HANDOFF_PURCHASE in payment_queue.action_required[0].actions


def test_settlement_adapter_exposes_expense_start_action() -> None:
    department = department_by_id("department_2")
    settlement = work_request_from_created(
        settlement_created_data(settlement_command(), department)
    )
    queue = build_user_work_queue(
        "U_STUDENT",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[],
        actionable_work_requests=[settlement],
    )
    assert WorkItemAction.START_SETTLEMENT in queue.action_required[0].actions


def test_home_renders_active_and_action_required_queues() -> None:
    department = department_by_id("department_2")
    settlement = work_request_from_created(
        settlement_created_data(settlement_command(), department)
    )
    queue = build_user_work_queue(
        "U_STUDENT",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[settlement],
        actionable_work_requests=[settlement],
    )
    from app.domain.enums import UserRole
    from app.domain.models import UserProfile

    view = app_home_view(
        UserProfile("U_STUDENT", UserRole.REQUESTER),
        [],
        queue,
        can_submit_requests=True,
    )
    action_ids = {
        element["action_id"]
        for block in view["blocks"]
        for element in block.get("elements", [])
        if "action_id" in element
    }
    assert "view_work_request" in action_ids
    assert "start_assigned_settlement" in action_ids
    assert len(view["blocks"]) <= 100
