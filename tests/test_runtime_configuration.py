import pytest

import app.config.roles as role_config
from app.config.roles import (
    ADMIN_STAFF_ROLE,
    ASSIGN_SETTLEMENT,
    PROFESSOR_ROLE,
    ROLE_DEFINITION_SEEDS,
    STUDENT_COORDINATOR_ROLE,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    RoleDefinitionSeed,
    default_role_assignments,
    empty_role_set,
)
from app.config.settings import Settings
from app.exceptions import ApprovalPermissionError
from app.ledger.codec import encode_chunks
from app.ledger.repository import (
    _SHARED_CHANNEL_CACHE,
    _SHARED_CHANNEL_INFO_CACHE,
    _SHARED_CONFIG_CACHE,
    _SHARED_HISTORY_CACHE,
    _SHARED_MEMBER_CACHE,
    CONFIG_ROOT,
    ROLE_ASSIGNMENTS_SAVED,
    RULE_SAVED,
    SYSTEM_CONFIG_ROOT,
    SlackLedgerRepository,
)

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


def role_configuration() -> dict[str, dict[str, set[str]]]:
    return {
        WORKSPACE_ROLE_SCOPE: {
            **empty_role_set(),
            STUDENT_COORDINATOR_ROLE: {"U_COORDINATOR"},
            PROFESSOR_ROLE: {"U_PROFESSOR", "U_OTHER_PROFESSOR", "U_OUTSIDE_PROFESSOR"},
            ADMIN_STAFF_ROLE: {"U_ADMIN_STAFF"},
            SYSTEM_ADMIN_ROLE: {ROOT_ADMIN, "U_NEW_ADMIN"},
        },
    }


@pytest.mark.asyncio
async def test_system_snapshot_keeps_workflow_route_and_global_roles(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    initial = await ledger.get_rule("department_1", "supplies")
    assert initial.workflow_id == "academic_development_approval"
    assert initial.approval_channel_id is None
    assert not initial.is_complete

    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    saved = await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")

    assert saved.version == 1
    assert saved.is_complete
    assert [step.approver_slack_user_ids for step in saved.steps] == [
        ("U_COORDINATOR",),
        ("U_PROFESSOR",),
        ("U_ADMIN_STAFF",),
    ]
    assert "U_OUTSIDE_PROFESSOR" not in saved.steps[1].approver_slack_user_ids
    assert not slack_client.messages["C_APPROVAL"]
    assert any(
        message.get("metadata", {}).get("event_type") == SYSTEM_CONFIG_ROOT
        for message in slack_client.messages["C_SYSTEM"]
    )


@pytest.mark.asyncio
async def test_channel_membership_scopes_global_roles(slack_client, settings: Settings) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())

    await ledger.assert_can_submit_request("U_REQUESTER", "C_APPROVAL")
    with pytest.raises(ApprovalPermissionError):
        await ledger.assert_can_submit_request("U_REQUESTER", "C_DEPARTMENT_2")
    assert await ledger.settlement_assigner_ids("C_APPROVAL") == {
        "U_PROFESSOR",
        "U_ADMIN_STAFF",
    }
    assert await ledger.settlement_assigner_ids("C_DEPARTMENT_2") == {
        "U_OTHER_PROFESSOR",
    }
    assert await ledger.system_admin_ids() == {ROOT_ADMIN, "U_NEW_ADMIN"}


@pytest.mark.asyncio
async def test_system_channels_separate_config_audit_alerts_and_operations(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_system_channels(
        ROOT_ADMIN,
        audit_channel_id="C_AUDIT",
        alerts_channel_id="C_ALERTS",
        additional_operating_channel_ids=["C_WORK"],
    )
    await ledger.report_alert("test alert")
    await ledger.report_alert("test alert")

    assert await ledger.registered_operation_channel_ids() == ["C_WORK"]
    assert slack_client.messages["C_AUDIT"][-1]["metadata"]["event_type"] == ("system_audit_event")
    assert slack_client.messages["C_ALERTS"][-1]["text"] == "test alert"
    assert len(slack_client.messages["C_ALERTS"]) == 1
    assert all(
        message.get("metadata", {}).get("event_type") == SYSTEM_CONFIG_ROOT
        for message in slack_client.messages["C_SYSTEM"]
    )


@pytest.mark.asyncio
async def test_new_role_is_added_through_role_configuration_only(
    slack_client, settings: Settings, monkeypatch
) -> None:
    department_head = RoleDefinitionSeed(
        id="DEPARTMENT_HEAD",
        name_en="Department Heads",
        name_ko="학과장",
        capabilities=frozenset({ASSIGN_SETTLEMENT}),
    )
    monkeypatch.setattr(
        role_config,
        "ROLE_DEFINITION_SEEDS",
        (*ROLE_DEFINITION_SEEDS, department_head),
    )
    assignments = role_configuration()
    assignments[WORKSPACE_ROLE_SCOPE][department_head.id] = {"U_DEPARTMENT_HEAD"}
    slack_client.channel_members["C_APPROVAL"].add("U_DEPARTMENT_HEAD")

    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_role_assignments(ROOT_ADMIN, assignments)

    assert "U_DEPARTMENT_HEAD" in await ledger.settlement_assigner_ids("C_APPROVAL")


@pytest.mark.asyncio
async def test_configuration_cold_read_is_one_channel_and_warm_read_is_zero(
    slack_client, settings: Settings
) -> None:
    for cache in (
        _SHARED_CHANNEL_CACHE,
        _SHARED_HISTORY_CACHE,
        _SHARED_CONFIG_CACHE,
        _SHARED_MEMBER_CACHE,
        _SHARED_CHANNEL_INFO_CACHE,
    ):
        cache.clear()

    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    for cache in (_SHARED_CONFIG_CACHE, _SHARED_HISTORY_CACHE):
        cache.clear()
    slack_client.calls.clear()

    await SlackLedgerRepository(slack_client, settings).role_assignments()
    first_calls = dict(slack_client.calls)
    await SlackLedgerRepository(slack_client, settings).role_assignments()

    assert first_calls == {"conversations_history": 1}
    assert dict(slack_client.calls) == first_calls


@pytest.mark.asyncio
async def test_legacy_distributed_configuration_is_migrated_once(
    slack_client, settings: Settings
) -> None:
    legacy_roles = encode_chunks(
        record_type=ROLE_ASSIGNMENTS_SAVED,
        data={
            "scopes": {
                "department_1": {
                    "REQUESTER": ["U_REQUESTER"],
                    STUDENT_COORDINATOR_ROLE: ["U_COORDINATOR"],
                    PROFESSOR_ROLE: ["U_PROFESSOR"],
                    ADMIN_STAFF_ROLE: ["U_ADMIN_STAFF"],
                }
            }
        },
    )[0]
    legacy_route = encode_chunks(
        record_type=RULE_SAVED,
        data={
            "department_id": "department_1",
            "budget_program_id": "department_budget",
            "category_id": "supplies",
            "approval_channel_id": "C_APPROVAL",
            "version": 3,
        },
    )[0]
    await slack_client.chat_postMessage(
        channel="C_APPROVAL",
        text="legacy roles",
        metadata={
            "event_type": CONFIG_ROOT,
            "event_payload": {
                "configuration_type": "access_roles",
                "key": "workspace",
                "inline_record": legacy_roles,
            },
        },
    )
    await slack_client.chat_postMessage(
        channel="C_APPROVAL",
        text="legacy route",
        metadata={
            "event_type": CONFIG_ROOT,
            "event_payload": {
                "configuration_type": "approval_route",
                "key": "department_1:supplies",
                "inline_record": legacy_route,
            },
        },
    )

    ledger = SlackLedgerRepository(slack_client, settings)
    assignments = await ledger.role_assignments()
    rule = await ledger.get_rule("department_1", "supplies")

    assert "U_REQUESTER" not in {
        user_id for users in assignments[WORKSPACE_ROLE_SCOPE].values() for user_id in users
    }
    assert rule.approval_channel_id == "C_APPROVAL"
    assert rule.version == 3
    assert rule.is_complete
    assert (
        sum(
            message.get("metadata", {}).get("event_type") == SYSTEM_CONFIG_ROOT
            for message in slack_client.messages["C_SYSTEM"]
        )
        == 1
    )
