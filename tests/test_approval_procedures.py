import pytest

from app.application.approval_procedures import (
    ApprovalProcedureService,
    ConfigureApprovalProcedureCommand,
)
from app.config.roles import (
    ADMIN_STAFF_ROLE,
    PROFESSOR_ROLE,
    REQUESTER_ROLE,
    STUDENT_COORDINATOR_ROLE,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
)
from app.exceptions import ConfigurationError
from app.ledger.repository import LedgerRepository
from tests.test_runtime_configuration import ROOT_ADMIN, role_configuration


@pytest.mark.asyncio
async def test_approval_procedure_changes_channel_and_step_assignees_atomically(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    service = ApprovalProcedureService(ledger)

    await service.configure(
        ConfigureApprovalProcedureCommand(
            actor_slack_user_id=ROOT_ADMIN,
            department_id="department_1",
            category_id="supplies",
            approval_channel_id="C_APPROVAL",
            assigned_user_ids_by_role={
                STUDENT_COORDINATOR_ROLE: ("U_COORDINATOR",),
                PROFESSOR_ROLE: ("U_PROFESSOR",),
                ADMIN_STAFF_ROLE: ("U_ADMIN_STAFF",),
            },
        )
    )

    configured = await service.get("department_1", "supplies")
    assert configured.approval_channel_id == "C_APPROVAL"
    assert configured.assigned_user_ids_by_role == {
        ADMIN_STAFF_ROLE: ("U_ADMIN_STAFF",),
        PROFESSOR_ROLE: ("U_PROFESSOR",),
        STUDENT_COORDINATOR_ROLE: ("U_COORDINATOR",),
    }
    resolved = await ledger.get_rule("department_1", "supplies")
    assert resolved.is_complete
    assert [step.approver_slack_user_ids for step in resolved.steps] == [
        ("U_COORDINATOR",),
        ("U_PROFESSOR",),
        ("U_ADMIN_STAFF",),
    ]

    all_roles = await ledger.role_assignments()
    workspace = all_roles[WORKSPACE_ROLE_SCOPE]
    assert workspace[REQUESTER_ROLE] == {"U_REQUESTER"}
    assert ROOT_ADMIN in workspace[SYSTEM_ADMIN_ROLE]


@pytest.mark.asyncio
async def test_approval_procedure_rejects_assignee_outside_selected_channel(
    slack_client, database
) -> None:
    service = ApprovalProcedureService(LedgerRepository(slack_client, database))

    with pytest.raises(ConfigurationError):
        await service.configure(
            ConfigureApprovalProcedureCommand(
                actor_slack_user_id=ROOT_ADMIN,
                department_id="department_1",
                category_id="supplies",
                approval_channel_id="C_APPROVAL",
                assigned_user_ids_by_role={
                    STUDENT_COORDINATOR_ROLE: ("U_COORDINATOR",),
                    PROFESSOR_ROLE: ("U_OUTSIDE_PROFESSOR",),
                    ADMIN_STAFF_ROLE: ("U_ADMIN_STAFF",),
                },
            )
        )
