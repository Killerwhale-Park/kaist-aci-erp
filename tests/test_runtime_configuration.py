import pytest
from sqlalchemy import func, select

import app.config.roles as role_config
from app.application.request_contexts import (
    ConfigureRequestContextCommand,
    RequestContextService,
)
from app.config.roles import (
    ADMIN_STAFF_ROLE,
    ASSIGN_SETTLEMENT,
    PROFESSOR_ROLE,
    REQUESTER_ROLE,
    ROLE_DEFINITION_SEEDS,
    STUDENT_COORDINATOR_ROLE,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    RoleDefinitionSeed,
    default_role_assignments,
    empty_role_set,
)
from app.exceptions import ApprovalPermissionError
from app.ledger.codec import encode_chunks
from app.ledger.repository import LedgerRepository
from app.ledger.tables import AuditEventRecord, RoleAssignmentRecord
from scripts.import_legacy_slack_config import (
    SYSTEM_CONFIG_ROOT,
    SYSTEM_CONFIG_SNAPSHOT,
    find_snapshot,
)

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


def role_configuration() -> dict[str, dict[str, set[str]]]:
    return {
        WORKSPACE_ROLE_SCOPE: {
            **empty_role_set(),
            REQUESTER_ROLE: {"U_REQUESTER"},
            STUDENT_COORDINATOR_ROLE: {"U_COORDINATOR"},
            PROFESSOR_ROLE: {"U_PROFESSOR", "U_OTHER_PROFESSOR", "U_OUTSIDE_PROFESSOR"},
            ADMIN_STAFF_ROLE: {"U_ADMIN_STAFF"},
            SYSTEM_ADMIN_ROLE: {ROOT_ADMIN, "U_NEW_ADMIN"},
        },
    }


@pytest.mark.asyncio
async def test_database_keeps_workflow_route_and_global_roles(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
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


@pytest.mark.asyncio
async def test_channel_membership_scopes_global_roles(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())

    await ledger.assert_can_submit_request("U_REQUESTER", "C_APPROVAL")
    with pytest.raises(ApprovalPermissionError):
        await ledger.assert_can_submit_request("U_REQUESTER", "C_DEPARTMENT_2")
    with pytest.raises(ApprovalPermissionError):
        await ledger.assert_can_submit_request("U_PROFESSOR", "C_APPROVAL")
    assert await ledger.settlement_assigner_ids("C_APPROVAL") == {
        "U_PROFESSOR",
        "U_ADMIN_STAFF",
    }
    assert await ledger.settlement_assigner_ids("C_DEPARTMENT_2") == {"U_OTHER_PROFESSOR"}
    assert await ledger.system_admin_ids() == {ROOT_ADMIN, "U_NEW_ADMIN"}


@pytest.mark.asyncio
async def test_bound_workflow_actor_requires_role_but_not_delivery_channel_membership(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())

    resolved = await ledger.resolve_approval_workflow(
        "purchase_payment_approval",
        "C_APPROVAL",
        actor_bindings={"payment_assignee": {"U_PROFESSOR"}},
    )
    outside = await ledger.resolve_approval_workflow(
        "purchase_payment_approval",
        "C_APPROVAL",
        actor_bindings={"payment_assignee": {"U_OUTSIDE_PROFESSOR"}},
    )
    wrong_role = await ledger.resolve_approval_workflow(
        "purchase_payment_approval",
        "C_APPROVAL",
        actor_bindings={"payment_assignee": {"U_REQUESTER"}},
    )

    assert resolved.is_complete
    assert resolved.steps[0].approver_slack_user_ids == ("U_PROFESSOR",)
    assert outside.is_complete
    assert outside.steps[0].approver_slack_user_ids == ("U_OUTSIDE_PROFESSOR",)
    assert not wrong_role.is_complete


@pytest.mark.asyncio
async def test_request_context_persists_defaults_without_creating_an_approval_route(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    service = RequestContextService(ledger)

    saved = await service.configure(
        ConfigureRequestContextCommand(
            actor_slack_user_id=ROOT_ADMIN,
            conversation_id="C_WORK",
            department_id="department_1",
            budget_node_id="supplies",
        )
    )
    loaded = await service.get("C_WORK")
    approval_rule = await ledger.get_rule("department_1", "supplies")

    assert loaded == saved
    assert loaded.department_id == "department_1"
    assert loaded.budget_node_id == "supplies"
    assert approval_rule.approval_channel_id is None

    with pytest.raises(ApprovalPermissionError):
        await service.configure(
            ConfigureRequestContextCommand(
                actor_slack_user_id="U_REQUESTER",
                conversation_id="C_WORK",
                department_id="department_1",
                budget_node_id="supplies",
            )
        )


@pytest.mark.asyncio
async def test_system_channels_use_database_and_slack_only_as_projection(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_system_channels(
        ROOT_ADMIN,
        audit_channel_id="C_AUDIT",
        alerts_channel_id="C_ALERTS",
        additional_operating_channel_ids=["C_WORK"],
    )
    await ledger.report_alert("test alert")
    await ledger.report_alert("test alert")

    assert await ledger.registered_operation_channel_ids() == ["C_WORK"]
    assert slack_client.messages["C_AUDIT"][-1]["text"].startswith(
        "System channel configuration updated"
    )
    assert slack_client.messages["C_ALERTS"][-1]["text"] == "test alert"
    assert len(slack_client.messages["C_ALERTS"]) == 1
    async with database.session() as session:
        audit_count = await session.scalar(select(func.count()).select_from(AuditEventRecord))
    assert audit_count == 2


@pytest.mark.asyncio
async def test_new_role_is_added_through_role_configuration_only(
    slack_client, database, monkeypatch
) -> None:
    department_head = RoleDefinitionSeed(
        id="DEPARTMENT_HEAD",
        name_en="Department Heads",
        name_ko="Department Heads",
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

    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, assignments)

    assert "U_DEPARTMENT_HEAD" in await ledger.settlement_assigner_ids("C_APPROVAL")


@pytest.mark.asyncio
async def test_configuration_survives_new_repository_and_empty_slack_history(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    slack_client.messages.clear()

    reloaded = LedgerRepository(slack_client, database)
    assignments = await reloaded.role_assignments()
    rule = await reloaded.get_rule("department_1", "supplies")

    assert assignments[WORKSPACE_ROLE_SCOPE][PROFESSOR_ROLE] >= {"U_PROFESSOR"}
    assert rule.approval_channel_id == "C_APPROVAL"
    assert rule.is_complete
    assert slack_client.calls["conversations_history"] == 0
    async with database.session() as session:
        stored_roles = await session.scalar(select(func.count()).select_from(RoleAssignmentRecord))
    assert stored_roles > 0


@pytest.mark.asyncio
async def test_legacy_system_snapshot_can_be_read_for_one_time_import(slack_client) -> None:
    encoded = encode_chunks(
        record_type=SYSTEM_CONFIG_SNAPSHOT,
        data={"roles": {PROFESSOR_ROLE: ["U_PROFESSOR"]}, "approval_routes": {}},
    )
    assert len(encoded) == 1
    await slack_client.chat_postMessage(
        channel="C_SYSTEM",
        text="legacy snapshot",
        metadata={
            "event_type": SYSTEM_CONFIG_ROOT,
            "event_payload": {"inline_record": encoded[0]},
        },
    )

    snapshot = await find_snapshot(slack_client, "C_SYSTEM")

    assert snapshot["roles"][PROFESSOR_ROLE] == ["U_PROFESSOR"]
