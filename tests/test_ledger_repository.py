import hashlib

import pytest
from sqlalchemy import func, select

from app.config.roles import SYSTEM_ADMIN_ROLE, WORKSPACE_ROLE_SCOPE, default_role_assignments
from app.domain.workflow import APPROVAL_STEP_APPROVED
from app.ledger.repository import LedgerRepository
from app.ledger.tables import ExpenseEventRecord
from tests.test_approval_workflow import make_created

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


async def register_test_channels(ledger: LedgerRepository) -> None:
    await ledger.replace_system_channels(
        ROOT_ADMIN,
        audit_channel_id="C_AUDIT",
        alerts_channel_id="C_ALERTS",
        additional_operating_channel_ids=["C_APPROVAL", "C_DEPARTMENT_2"],
    )


@pytest.mark.asyncio
async def test_expense_round_trip_and_indexed_filtering(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    created = await ledger.create_request(make_created(2))
    assert created.reference_number == "EXP-TEST-1"
    assert created.message_ts is None

    await ledger.update_request_view(created, text="Expense", blocks=[])
    assert created.message_ts
    own = await ledger.list_for_applicant("U_STUDENT")
    pending = await ledger.list_pending_for_actor("U_APPROVER_1")
    assert [item.id for item in own] == ["REQ-1"]
    assert [item.id for item in pending] == ["REQ-1"]

    updated = await ledger.append_event("REQ-1", APPROVAL_STEP_APPROVED, "U_APPROVER_1")
    assert updated.current_step_order == 2
    assert await ledger.list_pending_for_actor("U_APPROVER_1") == []
    assert [item.id for item in await ledger.list_pending_for_actor("U_APPROVER_2")] == ["REQ-1"]


@pytest.mark.asyncio
async def test_large_payload_is_stored_as_one_database_event(slack_client, database) -> None:
    created_data = make_created(1)
    created_data["purpose"] = "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(1000)
    )
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    created = await ledger.create_request(created_data)
    loaded = await ledger.get_request(created.id)

    assert loaded.purpose == created_data["purpose"]
    async with database.session() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ExpenseEventRecord)
            .where(ExpenseEventRecord.request_id == created.id)
        )
    assert count == 1


@pytest.mark.asyncio
async def test_legacy_slack_locator_resolves_by_database_id(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    created = await ledger.create_request(make_created(1))

    loaded = await ledger.get_request(f"C_APPROVAL|123.456|{created.id}")

    assert loaded.id == created.id
    assert slack_client.calls["conversations_history"] == 0
    assert slack_client.calls["conversations_replies"] == 0


@pytest.mark.asyncio
async def test_requests_are_persisted_independently_of_slack_channels(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    first_data = make_created(1)
    second_data = make_created(1)
    second_data["id"] = "REQ-2"
    second_data["reference_number"] = "EXP-TEST-2"
    second_data["approval_channel_id"] = "C_DEPARTMENT_2"

    first = await ledger.create_request(first_data)
    second = await ledger.create_request(second_data)
    slack_client.messages.clear()

    assert (await ledger.get_request(first.id)).approval_channel_id == "C_APPROVAL"
    assert (await ledger.get_request(second.id)).approval_channel_id == "C_DEPARTMENT_2"
    assert {request.id for request in await ledger.list_for_applicant("U_STUDENT")} == {
        "REQ-1",
        "REQ-2",
    }
