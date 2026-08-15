from datetime import date

import pytest
from pydantic import ValidationError
from slack_sdk.models.views import View

from app.config.roles import SYSTEM_ADMIN_ROLE, WORKSPACE_ROLE_SCOPE, default_role_assignments
from app.domain.catalog import department_by_id
from app.domain.enums import ApplicantType, WorkRequestKind, WorkRequestStatus
from app.domain.models import ApplicantProfile, ApprovalRuleStep, ResolvedApprovalWorkflow
from app.domain.work_requests import (
    WORK_APPROVAL_STEP_APPROVED,
    purchase_created_data,
    settlement_created_data,
    work_request_from_created,
)
from app.exceptions import ApprovalPermissionError, InvalidStateTransitionError
from app.ledger.repository import LedgerRepository
from app.slack.messages import work_request_blocks
from app.slack.modals import (
    expense_context_modal,
    expense_details_modal,
    purchase_request_modal,
    settlement_request_modal,
)
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand
from tests.test_approval_workflow import make_created

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


async def register_test_channels(ledger: LedgerRepository) -> None:
    await ledger.replace_system_channels(
        ROOT_ADMIN,
        audit_channel_id="C_AUDIT",
        alerts_channel_id="C_ALERTS",
        additional_operating_channel_ids=["C_APPROVAL", "C_DEPARTMENT_2"],
    )


def purchase_command() -> CreatePurchaseRequestCommand:
    return CreatePurchaseRequestCommand(
        requester_slack_user_id="U_STUDENT",
        department_id="department_1",
        assignee_slack_user_id="U_PROF",
        channel_id="C_APPROVAL",
        item_name="USB-C hub",
        product_url="https://www.coupang.com/example",
        quantity=2,
        estimated_amount="49000",
        purpose="수업 장비 연결",
    )


def purchase_workflow(*approvers: str) -> ResolvedApprovalWorkflow:
    return ResolvedApprovalWorkflow(
        id="purchase_payment_approval",
        name_en="Purchase Payment Approval",
        name_ko="구매 결제 승인",
        steps=tuple(
            ApprovalRuleStep(
                name_en=f"Approval {index}",
                name_ko=f"승인 {index}",
                approver_slack_user_ids=(approver,),
            )
            for index, approver in enumerate(approvers, start=1)
        ),
    )


def settlement_command() -> CreateSettlementRequestCommand:
    return CreateSettlementRequestCommand(
        requester_slack_user_id="U_ADMIN",
        department_id="department_2",
        assignee_slack_user_id="U_STUDENT",
        channel_id="C_DEPARTMENT_2",
        subject="Lab supplies",
        vendor="Coupang",
        amount="49000",
        payment_date=date(2026, 8, 13),
        purpose="구매 완료 물품 정산",
        evidence_folder_url="https://drive.google.com/drive/folders/example",
    )


def test_work_request_commands_require_https_urls() -> None:
    data = purchase_command().model_dump()
    data["product_url"] = "http://www.coupang.com/example"
    with pytest.raises(ValidationError):
        CreatePurchaseRequestCommand(**data)

    settlement = settlement_command().model_dump()
    settlement["evidence_folder_url"] = "not-a-url"
    with pytest.raises(ValidationError):
        CreateSettlementRequestCommand(**settlement)


@pytest.mark.asyncio
async def test_purchase_request_n_step_approval_and_settlement_handoff(
    slack_client, database
) -> None:
    department = department_by_id("department_1")
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    created = await ledger.create_work_request(
        purchase_created_data(
            purchase_command(),
            department,
            purchase_workflow("U_APPROVER_1", "U_PROF"),
        )
    )

    assert created.kind == WorkRequestKind.PURCHASE
    assert created.status == WorkRequestStatus.IN_APPROVAL
    assert created.current_step_order == 1
    assert created.subject == "USB-C hub"
    assert created.quantity == 2
    assert created.amount == 49000

    with pytest.raises(ApprovalPermissionError):
        await ledger.complete_work_request(created.slack_locator, "U_STRANGER")

    with pytest.raises(InvalidStateTransitionError):
        await ledger.complete_work_request(created.slack_locator, "U_PROF")

    approved_once = await ledger.append_work_event(
        created.id,
        WORK_APPROVAL_STEP_APPROVED,
        "U_APPROVER_1",
    )
    assert approved_once.current_step_order == 2
    approved = await ledger.append_work_event(
        created.id,
        WORK_APPROVAL_STEP_APPROVED,
        "U_PROF",
    )
    assert approved.status == WorkRequestStatus.ACTION_REQUIRED

    handoff_command = CreateSettlementRequestCommand(
        requester_slack_user_id="U_PROF",
        department_id="department_1",
        assignee_slack_user_id="U_STUDENT",
        channel_id="C_APPROVAL",
        subject=approved.subject,
        vendor="Coupang",
        amount="49000",
        payment_date=date(2026, 8, 14),
        purpose=approved.purpose,
        evidence_folder_url="https://drive.google.com/drive/folders/example",
    )
    handoff_data = settlement_created_data(
        handoff_command,
        department,
        originator_slack_user_id=approved.originator_slack_user_id,
        case_id=approved.case_id,
        parent_request_id=approved.id,
    )
    completed, settlement = await ledger.handoff_work_request(
        approved.id,
        "U_PROF",
        handoff_data,
    )

    assert completed.status == WorkRequestStatus.COMPLETED
    assert settlement.status == WorkRequestStatus.ACTION_REQUIRED
    assert settlement.parent_request_id == completed.id
    assert settlement.case_id == completed.case_id
    assert settlement.originator_slack_user_id == "U_STUDENT"
    assert [item.id for item in await ledger.list_active_work_for_user("U_STUDENT")] == [
        settlement.id
    ]
    assert [item.id for item in await ledger.list_active_work_for_user("U_PROF")] == [settlement.id]
    assert [item.id for item in await ledger.list_actionable_work_for_actor("U_STUDENT")] == [
        settlement.id
    ]


@pytest.mark.asyncio
async def test_settlement_assignment_is_stored_in_selected_channel(slack_client, database) -> None:
    department = department_by_id("department_2")
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    created = await ledger.create_work_request(
        settlement_created_data(settlement_command(), department)
    )

    assert created.kind == WorkRequestKind.SETTLEMENT
    assert created.channel_id == "C_DEPARTMENT_2"
    assert created.assignee_slack_user_id == "U_STUDENT"
    assert created.status == WorkRequestStatus.ACTION_REQUIRED
    assert created.message_ts is None
    await ledger.update_work_request_view(created, text="Settlement", blocks=[])
    assert created.message_ts
    assert any(
        element.get("action_id") == "start_assigned_settlement"
        for block in work_request_blocks(created)
        for element in block.get("elements", [])
    )
    with pytest.raises(InvalidStateTransitionError):
        await ledger.complete_work_request(created.id, "U_STUDENT")

    expense_data = make_created(1)
    expense_data["source_work_request_id"] = created.id
    expense_data["case_id"] = created.case_id
    expense = await ledger.create_request(expense_data)
    completed = await ledger.complete_work_request(
        created.id,
        "U_STUDENT",
        successor_type="EXPENSE_REQUEST",
        successor_id=expense.id,
    )
    assert expense.source_work_request_id == created.id
    assert expense.case_id == created.case_id
    assert completed.successor_type == "EXPENSE_REQUEST"
    assert completed.successor_id == expense.id


def test_work_request_modals_and_department_prefill() -> None:
    department = department_by_id("department_3")
    purchase = purchase_request_modal([department])
    settlement = settlement_request_modal([department])
    expense = expense_context_modal(
        ApplicantProfile("U_STUDENT", ApplicantType.STUDENT, "202500001"),
        [department],
        [],
        initial_department_id="department_3",
    )

    assert purchase["callback_id"] == "purchase_request_create"
    assert settlement["callback_id"] == "settlement_request_create"
    assert all(len(view["blocks"]) <= 100 for view in (purchase, settlement, expense))
    department_select = expense["blocks"][1]["element"]
    assert department_select["initial_option"]["value"] == "department_3"

    for modal in (purchase, settlement, expense):
        slack_view = View(**modal)
        assert slack_view.validate_json() is None


def test_settlement_assignment_prefills_expense_details() -> None:
    department = department_by_id("department_2")
    assignment = work_request_from_created(
        settlement_created_data(settlement_command(), department)
    )
    modal = expense_details_modal({"source_work_request_id": assignment.id}, [], assignment)
    values = {
        block["block_id"]: block["element"].get("initial_value")
        or block["element"].get("initial_date")
        for block in modal["blocks"]
        if "block_id" in block and "element" in block
    }

    assert values["amount"] == "49000"
    assert values["vendor"] == "Coupang"
    assert values["payment_date"] == "2026-08-13"
    assert values["purpose"] == "구매 완료 물품 정산"
    assert values["evidence_folder"] == "https://drive.google.com/drive/folders/example"
