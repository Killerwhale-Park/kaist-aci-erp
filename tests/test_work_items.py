from slack_sdk.models.views import View

from app.application.dashboard import DashboardCapabilities, UserDashboard
from app.application.work_items import WorkItemAction, build_user_work_queue
from app.domain.catalog import category_for_budget_node, department_by_id
from app.domain.enums import ApplicantType
from app.domain.models import ApplicantProfile
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
        settlement_created_data(
            settlement_command(),
            department,
            category_for_budget_node("supplies", "department_2"),
            "C_DEPARTMENT_2",
        )
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
        settlement_created_data(
            settlement_command(),
            department,
            category_for_budget_node("supplies", "department_2"),
            "C_DEPARTMENT_2",
        )
    )
    queue = build_user_work_queue(
        "U_STUDENT",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[settlement],
        actionable_work_requests=[settlement],
    )
    view = app_home_view(
        UserDashboard(
            slack_user_id="U_STUDENT",
            applicant_profile=ApplicantProfile("U_STUDENT", ApplicantType.STUDENT, "202600001"),
            work_queue=queue,
            capabilities=DashboardCapabilities(
                can_request=True,
                expense_ready=True,
                purchase_ready=True,
                can_assign_settlement=False,
                can_manage_configuration=False,
            ),
        )
    )
    action_ids = {
        element["action_id"]
        for block in view["blocks"]
        for element in block.get("elements", [])
        if "action_id" in element
    }
    assert "view_work_request" in action_ids
    assert "start_assigned_settlement" in action_ids
    references = [
        block.get("text", {}).get("text", "")
        for block in view["blocks"]
        if settlement.reference_number in block.get("text", {}).get("text", "")
    ]
    rendered = str(view)
    assert len(references) == 1
    assert "Received & Action Required" in rendered
    assert "내가 받은 요청·할 일" in rendered
    assert "Settlement Request" in rendered
    assert "정산 요청" in rendered
    assert "Start Expense" in rendered
    assert "정산 작성" in rendered
    assert len(view["blocks"]) <= 100
    slack_view = View(**view)
    assert slack_view.validate_json() is None


def test_home_only_offers_request_types_with_runtime_configuration() -> None:
    queue = build_user_work_queue(
        "U_ADMIN",
        own_expenses=[],
        pending_expense_approvals=[],
        submitted_work_requests=[],
        actionable_work_requests=[],
    )
    view = app_home_view(
        UserDashboard(
            slack_user_id="U_ADMIN",
            applicant_profile=None,
            work_queue=queue,
            capabilities=DashboardCapabilities(
                can_request=True,
                expense_ready=False,
                purchase_ready=True,
                can_assign_settlement=True,
                can_manage_configuration=True,
            ),
        ),
    )
    action_ids = {
        element["action_id"]
        for block in view["blocks"]
        for element in (
            block.get("elements", []) + ([block["accessory"]] if block.get("accessory") else [])
        )
        if element.get("action_id")
    }
    page_text = "\n".join(block.get("text", {}).get("text", "") for block in view["blocks"])

    assert "new_purchase_work_request" in action_ids
    assert "new_settlement_work_request" in action_ids
    assert "new_expense_request" in action_ids
    assert "refresh_home" in action_ids
    assert "configure_applicant_profile" in action_ids
    assert "manage_rules" in action_ids
    assert "approval procedures" in page_text
    assert "승인 절차" in page_text
