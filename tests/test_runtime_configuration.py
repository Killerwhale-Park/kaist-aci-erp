import pytest

from app.config.settings import Settings
from app.domain.catalog import default_rule
from app.domain.models import ApprovalRule, ApprovalRuleStep
from app.ledger.repository import SlackLedgerRepository


@pytest.mark.asyncio
async def test_rule_and_administrators_are_stored_in_slack_messages(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    initial = await ledger.get_rule("department_1", "supplies")
    assert initial == default_rule("department_1", "supplies")
    assert not initial.is_complete

    saved = await ledger.save_rule(
        "U_ADMIN",
        ApprovalRule(
            department_id="department_1",
            budget_program_id="department_budget",
            category_id="supplies",
            approval_channel_id="C_APPROVAL",
            steps=(
                ApprovalRuleStep("Professor", "교수", ("U_PROF", "U_PROF_2")),
                ApprovalRuleStep("Administration", "행정", ("U_ADMIN_REVIEW",)),
            ),
        ),
    )
    loaded = await ledger.get_rule("department_1", "supplies")
    assert loaded == saved
    assert loaded.version == 1
    assert loaded.is_complete
    assert slack_client.messages["C_APPROVAL"]

    await ledger.replace_system_admins("U_ADMIN", ["U_NEW_ADMIN"])
    assert await ledger.system_admin_ids() == {"U_NEW_ADMIN"}
    assert slack_client.messages["C_DEPARTMENT_2"]


@pytest.mark.asyncio
async def test_bootstrap_admin_is_used_only_until_runtime_record_exists(
    slack_client, settings: Settings
) -> None:
    ledger = SlackLedgerRepository(slack_client, settings)
    assert await ledger.system_admin_ids() == {"U_ADMIN"}
    await ledger.replace_system_admins("U_ADMIN", ["U_RUNTIME"])
    assert await ledger.system_admin_ids() == {"U_RUNTIME"}
