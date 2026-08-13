from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.catalog import department_by_id
from app.domain.enums import WorkRequestKind, WorkRequestStatus
from app.domain.work_requests import (
    purchase_created_data,
    settlement_created_data,
    work_request_from_created,
)
from app.exceptions import ApprovalPermissionError, InvalidStateTransitionError
from app.ledger.repository import SlackLedgerRepository
from app.slack.messages import work_request_blocks
from app.slack.modals import (
    expense_context_modal,
    expense_details_modal,
    purchase_request_modal,
    settlement_request_modal,
)
from app.work_requests import CreatePurchaseRequestCommand, CreateSettlementRequestCommand


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
async def test_purchase_request_round_trip_and_completion(slack_client, settings) -> None:
    department = department_by_id("department_1")
    ledger = SlackLedgerRepository(slack_client, settings)
    created = await ledger.create_work_request(
        purchase_created_data(purchase_command(), department)
    )

    assert created.kind == WorkRequestKind.PURCHASE
    assert created.status == WorkRequestStatus.OPEN
    assert created.subject == "USB-C hub"
    assert created.quantity == 2
    assert created.amount == 49000

    with pytest.raises(ApprovalPermissionError):
        await ledger.complete_work_request(created.id, "U_STRANGER")

    completed = await ledger.complete_work_request(created.id, "U_PROF")
    assert completed.status == WorkRequestStatus.COMPLETED
    assert completed.completed_by_slack_user_id == "U_PROF"
    with pytest.raises(InvalidStateTransitionError):
        await ledger.complete_work_request(created.id, "U_PROF")


@pytest.mark.asyncio
async def test_settlement_assignment_is_stored_in_selected_channel(slack_client, settings) -> None:
    department = department_by_id("department_2")
    ledger = SlackLedgerRepository(slack_client, settings)
    created = await ledger.create_work_request(
        settlement_created_data(settlement_command(), department)
    )

    assert created.kind == WorkRequestKind.SETTLEMENT
    assert created.channel_id == "C_DEPARTMENT_2"
    assert created.assignee_slack_user_id == "U_STUDENT"
    assert any(
        message["ts"] == created.message_ts for message in slack_client.messages["C_DEPARTMENT_2"]
    )
    assert any(
        element.get("action_id") == "start_assigned_settlement"
        for block in work_request_blocks(created)
        for element in block.get("elements", [])
    )


def test_work_request_modals_and_department_prefill() -> None:
    department = department_by_id("department_3")
    purchase = purchase_request_modal([department])
    settlement = settlement_request_modal([department])
    expense = expense_context_modal(
        "U_STUDENT", [department], [], initial_department_id="department_3"
    )

    assert purchase["callback_id"] == "purchase_request_create"
    assert settlement["callback_id"] == "settlement_request_create"
    assert all(len(view["blocks"]) <= 100 for view in (purchase, settlement, expense))
    department_select = expense["blocks"][1]["element"]
    assert department_select["initial_option"]["value"] == "department_3"


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
