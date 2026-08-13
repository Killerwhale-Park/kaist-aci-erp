import hashlib

import pytest

from app.config.roles import SYSTEM_ADMIN_ROLE, WORKSPACE_ROLE_SCOPE, default_role_assignments
from app.config.settings import Settings
from app.domain.workflow import APPROVAL_STEP_APPROVED
from app.ledger.repository import SlackLedgerRepository
from tests.test_approval_workflow import make_created

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


async def register_test_channels(ledger: SlackLedgerRepository) -> None:
    await ledger.replace_system_channels(
        ROOT_ADMIN,
        audit_channel_id="C_AUDIT",
        alerts_channel_id="C_ALERTS",
        additional_operating_channel_ids=["C_APPROVAL", "C_DEPARTMENT_2"],
    )


@pytest.mark.asyncio
async def test_expense_round_trip_and_history_filtering(slack_client, settings: Settings) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await register_test_channels(ledger)
    created = await ledger.create_request(make_created(2))
    assert created.reference_number == "EXP-TEST-1"
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
async def test_large_event_is_split_and_reassembled(slack_client, settings: Settings) -> None:
    created_data = make_created(1)
    created_data["purpose"] = "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(1000)
    )
    ledger = SlackLedgerRepository(slack_client, settings)
    await register_test_channels(ledger)
    created = await ledger.create_request(created_data)
    loaded = await ledger.get_request(created.slack_locator)
    assert loaded.purpose == created_data["purpose"]
    thread_messages = [
        item
        for item in slack_client.messages["C_APPROVAL"]
        if item.get("thread_ts") == created.message_ts
    ]
    assert len(thread_messages) > 1


@pytest.mark.asyncio
async def test_locator_reads_one_message_without_channel_discovery(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await register_test_channels(ledger)
    created = await ledger.create_request(make_created(1))
    slack_client.calls.clear()

    loaded = await ledger.get_request(created.slack_locator)

    assert loaded.id == created.id
    assert slack_client.calls["conversations_list"] == 0
    assert slack_client.calls["conversations_history"] == 1
    assert slack_client.calls["conversations_replies"] == 1


@pytest.mark.asyncio
async def test_requests_are_stored_in_each_rules_channel(slack_client, settings: Settings) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await register_test_channels(ledger)
    first_data = make_created(1)
    second_data = make_created(1)
    second_data["id"] = "REQ-2"
    second_data["reference_number"] = "EXP-TEST-2"
    second_data["approval_channel_id"] = "C_DEPARTMENT_2"

    first = await ledger.create_request(first_data)
    second = await ledger.create_request(second_data)

    assert first.approval_channel_id == "C_APPROVAL"
    assert second.approval_channel_id == "C_DEPARTMENT_2"
    assert any(message["ts"] == first.message_ts for message in slack_client.messages["C_APPROVAL"])
    assert any(
        message["ts"] == second.message_ts for message in slack_client.messages["C_DEPARTMENT_2"]
    )
    assert {request.id for request in await ledger.list_for_applicant("U_STUDENT")} == {
        "REQ-1",
        "REQ-2",
    }
