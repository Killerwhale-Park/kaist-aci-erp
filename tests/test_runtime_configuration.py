import pytest

import app.config.roles as role_config
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
from app.config.settings import Settings
from app.exceptions import ApprovalPermissionError
from app.ledger.repository import (
    _SHARED_CHANNEL_CACHE,
    _SHARED_HISTORY_CACHE,
    SlackLedgerRepository,
)

ROOT_ADMIN = next(iter(default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]))


def role_configuration() -> dict[str, dict[str, set[str]]]:
    return {
        WORKSPACE_ROLE_SCOPE: {
            **empty_role_set(),
            SYSTEM_ADMIN_ROLE: {ROOT_ADMIN, "U_NEW_ADMIN"},
        },
        "department_1": {
            **empty_role_set(),
            REQUESTER_ROLE: {"U_REQUESTER"},
            STUDENT_COORDINATOR_ROLE: {"U_COORDINATOR"},
            PROFESSOR_ROLE: {"U_PROFESSOR"},
            ADMIN_STAFF_ROLE: {"U_ADMIN_STAFF"},
        },
    }


@pytest.mark.asyncio
async def test_workflow_policy_is_code_and_route_and_roles_are_runtime_configuration(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    initial = await ledger.get_rule("department_1", "supplies")
    assert initial.workflow_id == "academic_development_approval"
    assert initial.approval_channel_id is None
    assert [role for step in initial.steps for role in step.approver_roles] == [
        STUDENT_COORDINATOR_ROLE,
        PROFESSOR_ROLE,
        ADMIN_STAFF_ROLE,
    ]
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
    assert slack_client.messages["C_APPROVAL"]


@pytest.mark.asyncio
async def test_access_roles_are_scoped_by_department(slack_client, settings: Settings) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())

    await ledger.assert_can_submit_request("U_REQUESTER", "department_1")
    with pytest.raises(ApprovalPermissionError):
        await ledger.assert_can_submit_request("U_REQUESTER", "department_2")
    assert await ledger.settlement_assigner_ids("department_1") == {
        ROOT_ADMIN,
        "U_NEW_ADMIN",
        "U_PROFESSOR",
        "U_ADMIN_STAFF",
    }
    assert await ledger.settlement_assigner_ids() == {
        ROOT_ADMIN,
        "U_NEW_ADMIN",
        "U_PROFESSOR",
        "U_ADMIN_STAFF",
    }
    assert await ledger.system_admin_ids() == {ROOT_ADMIN, "U_NEW_ADMIN"}


@pytest.mark.asyncio
async def test_new_role_is_added_through_role_configuration_only(
    slack_client, settings: Settings, monkeypatch
) -> None:
    department_head = RoleDefinitionSeed(
        id="DEPARTMENT_HEAD",
        name_en="Department Heads",
        name_ko="학과장",
        department_scoped=True,
        capabilities=frozenset({ASSIGN_SETTLEMENT}),
    )
    monkeypatch.setattr(
        role_config,
        "ROLE_DEFINITION_SEEDS",
        (*ROLE_DEFINITION_SEEDS, department_head),
    )
    assignments = role_configuration()
    assignments["department_1"][department_head.id] = {"U_DEPARTMENT_HEAD"}

    ledger = SlackLedgerRepository(slack_client, settings)
    await ledger.replace_role_assignments(ROOT_ADMIN, assignments)

    assert "U_DEPARTMENT_HEAD" in await ledger.settlement_assigner_ids("department_1")


@pytest.mark.asyncio
async def test_configuration_queries_are_reused_across_repository_instances(
    slack_client, settings: Settings
) -> None:
    _SHARED_CHANNEL_CACHE.clear()
    _SHARED_HISTORY_CACHE.clear()

    await SlackLedgerRepository(slack_client, settings).role_assignments()
    first_calls = dict(slack_client.calls)
    await SlackLedgerRepository(slack_client, settings).role_assignments()

    assert dict(slack_client.calls) == first_calls
