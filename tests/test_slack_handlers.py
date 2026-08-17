import json
from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import event

from app.application.request_contexts import (
    ConfigureRequestContextCommand,
    RequestContextService,
)
from app.domain.catalog import category_for_budget_node, department_by_id
from app.domain.enums import ApplicantType, RequestStatus, WorkRequestKind, WorkRequestStatus
from app.domain.work_requests import WORK_APPROVAL_STEP_APPROVED, settlement_created_data
from app.i18n import t
from app.ledger.repository import LedgerRepository
from app.slack.handlers import register_handlers
from app.slack.modals import loading_modal
from app.work_requests import CreateSettlementRequestCommand
from tests.test_ledger_repository import register_test_channels
from tests.test_runtime_configuration import ROOT_ADMIN, role_configuration
from tests.test_work_requests import settlement_command


class RegisteredHandlers:
    def __init__(self) -> None:
        self.actions: dict[str, Callable] = {}
        self.views: dict[str, Callable] = {}
        self.events: dict[str, Callable] = {}
        self.commands: dict[str, Callable] = {}

    @staticmethod
    def _decorator(target: dict[str, Callable], key: str):
        def register(function: Callable) -> Callable:
            target[key] = function
            return function

        return register

    def action(self, key: str):
        return self._decorator(self.actions, key)

    def view(self, key: str):
        return self._decorator(self.views, key)

    def event(self, key: str):
        return self._decorator(self.events, key)

    def command(self, key: str):
        return self._decorator(self.commands, key)


def registered_handlers(database) -> RegisteredHandlers:
    handlers = RegisteredHandlers()
    register_handlers(handlers, database)
    return handlers


@pytest.mark.asyncio
async def test_slash_command_reuses_conversation_request_context(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
    await RequestContextService(ledger).configure(
        ConfigureRequestContextCommand(
            actor_slack_user_id=ROOT_ADMIN,
            conversation_id="C_WORK",
            department_id="department_1",
            budget_node_id="supplies",
        )
    )
    await ledger.save_applicant_profile(
        "U_REQUESTER",
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        return None

    await handlers.commands["/expense"](
        ack,
        {
            "text": "",
            "channel_id": "C_WORK",
            "user_id": "U_REQUESTER",
            "trigger_id": "TRIGGER_CONTEXT",
        },
        slack_client,
    )

    view = slack_client.opened_views["V1"]
    assert view["callback_id"] == "expense_context"
    department = next(block for block in view["blocks"] if block.get("block_id") == "department")
    assert department["element"]["initial_option"]["value"] == "department_1"
    selected_budget_path = [
        block["element"]["initial_option"]["value"]
        for block in view["blocks"]
        if block.get("block_id", "").startswith("budget_level_")
    ]
    assert selected_budget_path == [
        "department_budget",
        "academic_development",
        "supplies",
    ]

    await handlers.commands["/expense"](
        ack,
        {
            "text": "setup",
            "channel_id": "C_WORK",
            "user_id": ROOT_ADMIN,
            "trigger_id": "TRIGGER_SETUP",
        },
        slack_client,
    )
    setup = slack_client.opened_views["V2"]
    assert setup["callback_id"] == "request_context_configure"
    assert setup["private_metadata"] == json.dumps({"conversation_id": "C_WORK"})


@pytest.mark.asyncio
async def test_purchase_command_reuses_personal_dm_context(slack_client, database) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await RequestContextService(ledger).configure(
        ConfigureRequestContextCommand(
            actor_slack_user_id="U_REQUESTER",
            conversation_id="D_APP",
            department_id="department_1",
            budget_node_id="supplies",
        )
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        return None

    await handlers.commands["/expense"](
        ack,
        {
            "text": "purchase",
            "channel_id": "D_APP",
            "user_id": "U_REQUESTER",
            "trigger_id": "TRIGGER_DM_PURCHASE",
        },
        slack_client,
    )
    modal = slack_client.opened_views["V1"]
    assert modal["callback_id"] == "purchase_request_create"
    assert json.loads(modal["private_metadata"])["source_conversation_id"] == "D_APP"
    channel = next(block for block in modal["blocks"] if block.get("block_id") == "work_channel")
    assert channel["element"]["initial_conversation"] == "D_APP"

    def plain(value: str) -> dict:
        return {"value": {"type": "plain_text_input", "value": value}}

    def selected(value: str, element_type: str) -> dict:
        key = {
            "users_select": "selected_user",
            "conversations_select": "selected_conversation",
        }.get(element_type, "selected_option")
        element = {"type": element_type}
        element[key] = {"value": value} if key == "selected_option" else value
        return {"value": element}

    await handlers.views["purchase_request_create"](
        ack,
        {
            "user": {"id": "U_REQUESTER"},
            "view": {
                "id": "V1",
                "private_metadata": modal["private_metadata"],
                "state": {
                    "values": {
                        "work_department": selected("department_1", "static_select"),
                        "purchase_assignee": selected("U_PROFESSOR", "users_select"),
                        "work_channel": selected("D_APP", "conversations_select"),
                        "item_name": plain("Keyboard"),
                        "product_url": plain("https://example.com/keyboard"),
                        "quantity": plain("1"),
                        "estimated_amount": plain("50000"),
                        "work_purpose": plain("DM context purchase"),
                    }
                },
            },
        },
        slack_client,
    )

    created = (await ledger.list_active_work_for_user("U_REQUESTER"))[0]
    assert created.channel_id == "D_APP"
    assert created.source_conversation_id == "D_APP"

    await ledger.append_work_event(
        created.id,
        WORK_APPROVAL_STEP_APPROVED,
        "U_PROFESSOR",
    )
    await handlers.actions["handoff_purchase_to_settlement"](
        ack,
        {
            "trigger_id": "TRIGGER_DM_HANDOFF",
            "user": {"id": "U_PROFESSOR"},
            "actions": [{"value": created.id}],
        },
        slack_client,
    )
    handoff = slack_client.opened_views["V2"]
    budget = next(
        block for block in handoff["blocks"] if block.get("block_id") == "work_budget_item"
    )
    assert budget["element"]["initial_option"]["value"] == "supplies"


@pytest.mark.asyncio
async def test_direct_expense_form_opens_without_settlement_or_approval_route(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.save_applicant_profile(
        "U_STUDENT",
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    await handlers.actions["new_expense_request"](
        ack,
        {
            "trigger_id": "TRIGGER",
            "user": {"id": "U_STUDENT"},
            "actions": [{"value": "new"}],
        },
        slack_client,
    )

    assert slack_client.call_order[:3] == ["ack", "views_open", "views_update"]
    assert slack_client.opened_views["V1"]["callback_id"] == "expense_context"


@pytest.mark.asyncio
async def test_missing_profile_is_saved_once_then_continues_to_expense(
    slack_client, database
) -> None:
    handlers = registered_handlers(database)

    async def action_ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    await handlers.actions["new_expense_request"](
        action_ack,
        {
            "trigger_id": "TRIGGER",
            "user": {"id": "U_STUDENT"},
            "actions": [{"value": "new"}],
        },
        slack_client,
    )
    assert slack_client.opened_views["V1"]["callback_id"] == "applicant_profile"

    acknowledgements: list[dict] = []

    async def view_ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    await handlers.views["applicant_profile"](
        view_ack,
        {
            "user": {"id": "U_STUDENT"},
            "view": {
                "id": "V1",
                "private_metadata": slack_client.opened_views["V1"]["private_metadata"],
                "state": {
                    "values": {
                        "profile_applicant_type": {
                            "profile_applicant_type_changed": {
                                "type": "static_select",
                                "selected_option": {"value": "STUDENT"},
                            }
                        },
                        "profile_identifier": {
                            "value": {
                                "type": "plain_text_input",
                                "value": "202600001",
                            }
                        },
                    }
                },
            },
        },
        slack_client,
    )

    profile = await LedgerRepository(slack_client, database).applicant_profile("U_STUDENT")
    assert profile is not None
    assert profile.applicant_identifier == "202600001"
    assert acknowledgements == [{"response_action": "update", "view": loading_modal(t("saving"))}]
    assert slack_client.opened_views["V1"]["callback_id"] == "expense_context"


@pytest.mark.asyncio
async def test_received_settlement_is_listed_and_can_start_after_loading_view(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    command = settlement_command().model_copy(
        update={
            "requester_slack_user_id": "U_OTHER_PROFESSOR",
            "assignee_slack_user_id": "U_COORDINATOR",
            "department_id": "department_1",
        }
    )
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    await ledger.save_applicant_profile(
        command.assignee_slack_user_id,
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    def plain(value: str) -> dict:
        return {"value": {"type": "plain_text_input", "value": value}}

    def selected(value: str, element_type: str) -> dict:
        key = {
            "users_select": "selected_user",
            "conversations_select": "selected_conversation",
        }.get(element_type)
        element = {"type": element_type}
        if key:
            element[key] = value
        else:
            element["selected_option"] = {"value": value}
        return {"value": element}

    await handlers.actions["new_settlement_work_request"](
        ack,
        {
            "trigger_id": "TRIGGER_SETTLEMENT_REQUEST",
            "user": {"id": command.requester_slack_user_id},
        },
        slack_client,
    )
    assert slack_client.opened_views["V1"]["callback_id"] == "settlement_request_create"
    await handlers.views["settlement_request_create"](
        ack,
        {
            "user": {"id": command.requester_slack_user_id},
            "view": {
                "id": "V1",
                "state": {
                    "values": {
                        "work_department": selected(command.department_id, "static_select"),
                        "work_budget_item": selected(command.budget_node_id, "static_select"),
                        "settlement_assignee": selected(
                            command.assignee_slack_user_id, "users_select"
                        ),
                        "work_subject": plain(command.subject),
                        "work_vendor": plain(command.vendor),
                        "work_amount": plain(str(command.amount)),
                        "work_payment_date": {
                            "value": {
                                "type": "datepicker",
                                "selected_date": command.payment_date.isoformat(),
                            }
                        },
                        "work_purpose": plain(command.purpose),
                        "work_evidence_folder": plain(command.evidence_folder_url or ""),
                    }
                },
            },
        },
        slack_client,
    )
    request = (await ledger.list_actionable_work_for_actor(command.assignee_slack_user_id))[0]
    assert request.kind == WorkRequestKind.SETTLEMENT

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(database.engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        await handlers.actions["refresh_home"](
            ack,
            {"user": {"id": command.assignee_slack_user_id}},
            slack_client,
        )
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", record_statement)
    assert sum("FROM work_request_events" in statement for statement in statements) == 1
    home = slack_client.published_views[command.assignee_slack_user_id]
    home_actions = {
        element["action_id"]
        for block in home["blocks"]
        for element in block.get("elements", [])
        if element.get("action_id")
    }
    assert "start_assigned_settlement" in home_actions

    slack_client.call_order.clear()
    await handlers.actions["start_assigned_settlement"](
        ack,
        {
            "trigger_id": "TRIGGER",
            "user": {"id": command.assignee_slack_user_id},
            "actions": [{"value": request.id}],
        },
        slack_client,
    )

    assert slack_client.call_order[:2] == ["ack", "views_open"]
    assert slack_client.opened_views["V2"]["callback_id"] == "expense_context"
    assert "source_work_request_id" in slack_client.opened_views["V2"]["private_metadata"]
    assert "locked_budget_node_ids" in slack_client.opened_views["V2"]["private_metadata"]
    assert t("assigned_budget_locked") in str(slack_client.opened_views["V2"])


@pytest.mark.asyncio
async def test_related_user_can_view_work_request_without_admin_lookup(
    slack_client, database, monkeypatch
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    command = settlement_command()
    request = await ledger.create_work_request(
        settlement_created_data(
            command,
            department_by_id(command.department_id),
            category_for_budget_node(command.budget_node_id, command.department_id),
            "C_DEPARTMENT_2",
        )
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    async def unexpected_admin_lookup(_self) -> set[str]:
        raise AssertionError("directly related users do not need an administrator query")

    monkeypatch.setattr(LedgerRepository, "system_admin_ids", unexpected_admin_lookup)

    await handlers.actions["view_work_request"](
        ack,
        {
            "trigger_id": "TRIGGER",
            "user": {"id": command.assignee_slack_user_id},
            "actions": [{"value": request.id}],
        },
        slack_client,
    )

    assert slack_client.call_order[:3] == ["ack", "views_open", "views_update"]
    assert slack_client.opened_views["V1"]["callback_id"] == "work_request_details"


@pytest.mark.asyncio
async def test_purchase_handlers_approve_handoff_and_reject_without_losing_work(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    await ledger.save_applicant_profile(
        "U_COORDINATOR",
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    async def respond(**_kwargs) -> None:
        return None

    def plain(value: str) -> dict:
        return {"value": {"type": "plain_text_input", "value": value}}

    def selected(value: str, element_type: str) -> dict:
        key = {
            "users_select": "selected_user",
            "conversations_select": "selected_conversation",
        }.get(element_type)
        element = {"type": element_type}
        if key:
            element[key] = value
        else:
            element["selected_option"] = {"value": value}
        return {"value": element}

    await handlers.views["purchase_request_create"](
        ack,
        {
            "user": {"id": "U_REQUESTER"},
            "view": {
                "id": "V_PURCHASE",
                "state": {
                    "values": {
                        "work_department": selected("department_1", "static_select"),
                        "purchase_assignee": selected("U_PROFESSOR", "users_select"),
                        "work_channel": selected("C_APPROVAL", "conversations_select"),
                        "item_name": plain("USB-C hub"),
                        "product_url": plain("https://example.com/item"),
                        "quantity": plain("2"),
                        "estimated_amount": plain("49000"),
                        "work_purpose": plain("Purchase handler integration"),
                    }
                },
            },
        },
        slack_client,
    )

    assert acknowledgements == [{"response_action": "update", "view": loading_modal(t("saving"))}]
    submitted = await ledger.list_active_work_for_user("U_REQUESTER")
    received = await ledger.list_actionable_work_for_actor("U_PROFESSOR")
    assert len(submitted) == 1
    assert [item.id for item in received] == [submitted[0].id]
    assert slack_client.opened_views["V_PURCHASE"]["callback_id"] == "configuration_notice"
    assert {"U_REQUESTER", "U_PROFESSOR"} <= slack_client.published_views.keys()

    purchase = submitted[0]
    await handlers.actions["approve_work_request"](
        ack,
        {
            "user": {"id": "U_PROFESSOR"},
            "actions": [{"value": purchase.id}],
        },
        slack_client,
        respond,
    )
    purchase = await ledger.get_work_request(purchase.id)
    assert purchase.status == WorkRequestStatus.ACTION_REQUIRED

    await handlers.actions["handoff_purchase_to_settlement"](
        ack,
        {
            "trigger_id": "TRIGGER_HANDOFF",
            "user": {"id": "U_PROFESSOR"},
            "actions": [{"value": purchase.id}],
        },
        slack_client,
    )
    handoff = slack_client.opened_views["V1"]
    assert handoff["callback_id"] == "purchase_settlement_handoff"
    assert not any(block.get("block_id") == "work_department" for block in handoff["blocks"])
    await handlers.views["purchase_settlement_handoff"](
        ack,
        {
            "user": {"id": "U_PROFESSOR"},
            "view": {
                "id": "V1",
                "private_metadata": handoff["private_metadata"],
                "state": {
                    "values": {
                        "work_budget_item": selected("supplies", "static_select"),
                        "settlement_assignee": selected("U_COORDINATOR", "users_select"),
                        "work_subject": plain("USB-C hub"),
                        "work_vendor": plain("Example Store"),
                        "work_amount": plain("49000"),
                        "work_payment_date": {
                            "value": {
                                "type": "datepicker",
                                "selected_date": "2026-08-15",
                            }
                        },
                        "work_purpose": plain("Settle the purchased equipment"),
                        "work_evidence_folder": plain(
                            "https://drive.google.com/drive/folders/purchase-handoff"
                        ),
                    }
                },
            },
        },
        slack_client,
    )
    purchase = await ledger.get_work_request(purchase.id)
    assert purchase.status == WorkRequestStatus.COMPLETED
    settlement = (await ledger.list_actionable_work_for_actor("U_COORDINATOR"))[0]
    assert settlement.kind == WorkRequestKind.SETTLEMENT
    assert settlement.parent_request_id == purchase.id
    assert settlement.case_id == purchase.case_id

    await handlers.actions["start_assigned_settlement"](
        ack,
        {
            "trigger_id": "TRIGGER_SETTLEMENT",
            "user": {"id": "U_COORDINATOR"},
            "actions": [{"value": settlement.id}],
        },
        slack_client,
    )
    settlement_context = slack_client.opened_views["V2"]
    assert settlement_context["callback_id"] == "expense_context"
    assert settlement.id in settlement_context["private_metadata"]

    await handlers.views["purchase_request_create"](
        ack,
        {
            "user": {"id": "U_REQUESTER"},
            "view": {
                "id": "V_REJECTED_PURCHASE",
                "state": {
                    "values": {
                        "work_department": selected("department_1", "static_select"),
                        "purchase_assignee": selected("U_PROFESSOR", "users_select"),
                        "work_channel": selected("C_APPROVAL", "conversations_select"),
                        "item_name": plain("Rejected item"),
                        "product_url": plain("https://example.com/rejected-item"),
                        "quantity": plain("1"),
                        "estimated_amount": plain("10000"),
                        "work_purpose": plain("Verify work-request rejection"),
                    }
                },
            },
        },
        slack_client,
    )
    rejected_purchase = next(
        item
        for item in await ledger.list_active_work_for_user("U_REQUESTER")
        if item.subject == "Rejected item"
    )
    await handlers.actions["reject_work_request"](
        ack,
        {
            "trigger_id": "TRIGGER_REJECT",
            "user": {"id": "U_PROFESSOR"},
            "actions": [{"value": rejected_purchase.id}],
        },
        slack_client,
    )
    rejection = slack_client.opened_views["V3"]
    await handlers.views["work_request_rejection"](
        ack,
        {
            "user": {"id": "U_PROFESSOR"},
            "view": {
                "id": "V3",
                "private_metadata": rejection["private_metadata"],
                "state": {"values": {"decision_reason": plain("Not approved")}},
            },
        },
        slack_client,
    )
    rejected_purchase = await ledger.get_work_request(rejected_purchase.id)
    assert rejected_purchase.status == WorkRequestStatus.REJECTED
    assert rejected_purchase.rejection_reason == "Not approved"


@pytest.mark.asyncio
async def test_administrator_can_save_roles_and_system_channels_from_slack(
    slack_client, database
) -> None:
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    await handlers.actions["configure_access_roles"](
        ack,
        {"trigger_id": "TRIGGER", "user": {"id": ROOT_ADMIN}},
        slack_client,
    )

    assert slack_client.call_order[:3] == ["ack", "views_push", "views_update"]
    assert slack_client.opened_views["VP1"]["callback_id"] == "access_roles_editor"

    assignments = role_configuration()["workspace"]
    await handlers.views["access_roles_editor"](
        ack,
        {
            "user": {"id": ROOT_ADMIN},
            "view": {
                "id": "VP1",
                "state": {
                    "values": {
                        f"access_role__workspace__{role_id}": {
                            "value": {
                                "type": "multi_users_select",
                                "selected_users": sorted(user_ids),
                            }
                        }
                        for role_id, user_ids in assignments.items()
                    }
                },
            },
        },
        slack_client,
    )
    ledger = LedgerRepository(slack_client, database)
    assert "U_REQUESTER" in (await ledger.role_assignments())["workspace"]["REQUESTER"]

    slack_client.call_order.clear()
    await handlers.actions["configure_system_channels"](
        ack,
        {"trigger_id": "TRIGGER_CHANNELS", "user": {"id": ROOT_ADMIN}},
        slack_client,
    )
    assert slack_client.call_order[:3] == ["ack", "views_push", "views_update"]
    assert slack_client.opened_views["VP2"]["callback_id"] == "system_channels_editor"
    await handlers.views["system_channels_editor"](
        ack,
        {
            "user": {"id": ROOT_ADMIN},
            "view": {
                "id": "VP2",
                "state": {
                    "values": {
                        "audit_channel": {
                            "value": {
                                "type": "conversations_select",
                                "selected_conversation": "C_AUDIT",
                            }
                        },
                        "alerts_channel": {
                            "value": {
                                "type": "conversations_select",
                                "selected_conversation": "C_ALERTS",
                            }
                        },
                        "additional_operating_channels": {
                            "value": {
                                "type": "multi_conversations_select",
                                "selected_conversations": ["C_WORK"],
                            }
                        },
                    }
                },
            },
        },
        slack_client,
    )
    channels = await ledger.system_channels()
    assert channels == {
        "audit_channel_id": "C_AUDIT",
        "alerts_channel_id": "C_ALERTS",
        "additional_operating_channel_ids": ["C_WORK"],
    }


@pytest.mark.asyncio
async def test_approval_procedure_navigation_uses_submission_response_only(
    slack_client, database
) -> None:
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    await handlers.views["administration_menu"](
        ack,
        {
            "user": {"id": ROOT_ADMIN},
            "view": {
                "state": {
                    "values": {
                        "administration_section": {
                            "value": {
                                "type": "static_select",
                                "selected_option": {"value": "approval_procedure"},
                            }
                        }
                    }
                }
            },
        },
        slack_client,
    )

    assert len(acknowledgements) == 1
    assert acknowledgements[0]["response_action"] == "update"
    assert acknowledgements[0]["view"]["callback_id"] == "approval_rule_selector"
    assert slack_client.calls["views_push"] == 0
    assert slack_client.calls["views_update"] == 0


@pytest.mark.asyncio
async def test_approval_procedure_selection_loads_saved_channel_and_assignees(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    def selected(value: str) -> dict:
        return {
            "value": {
                "type": "static_select",
                "selected_option": {"value": value},
            }
        }

    await handlers.views["approval_rule_selector"](
        ack,
        {
            "user": {"id": ROOT_ADMIN},
            "view": {
                "id": "V_APPROVAL",
                "state": {
                    "values": {
                        "rule_department": selected("department_1"),
                        "rule_category": selected("supplies"),
                    }
                },
            },
        },
        slack_client,
    )

    assert len(acknowledgements) == 1
    response = acknowledgements[0]
    assert response["response_action"] == "update"
    assert response["view"]["callback_id"] == "loading"
    editor = slack_client.opened_views["V_APPROVAL"]
    assert editor["callback_id"] == "approval_rule_editor"
    visible_text = str(
        [
            block.get("text") or block.get("label") or block.get("element", {}).get("placeholder")
            for block in editor["blocks"]
        ]
    )
    assert "학생 담당자" in visible_text
    assert "STUDENT_COORDINATOR" not in visible_text
    channel = next(
        block for block in editor["blocks"] if block.get("block_id") == "approval_channel"
    )
    assert channel["element"]["initial_conversation"] == "C_APPROVAL"
    coordinator = next(
        block
        for block in editor["blocks"]
        if block.get("block_id") == "approval_role__STUDENT_COORDINATOR"
    )
    assert coordinator["element"]["initial_users"] == ["U_COORDINATOR"]


@pytest.mark.asyncio
async def test_approval_procedure_save_changes_channel_and_step_assignees(
    slack_client, database
) -> None:
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    await handlers.views["approval_rule_editor"](
        ack,
        {
            "user": {"id": ROOT_ADMIN},
            "view": {
                "private_metadata": json.dumps(
                    {"department_id": "department_1", "category_id": "supplies"}
                ),
                "id": "V_APPROVAL",
                "state": {
                    "values": {
                        "approval_channel": {
                            "value": {
                                "type": "conversations_select",
                                "selected_conversation": "C_APPROVAL",
                            }
                        },
                        "approval_role__STUDENT_COORDINATOR": {
                            "value": {
                                "type": "multi_users_select",
                                "selected_users": ["U_COORDINATOR"],
                            }
                        },
                        "approval_role__PROFESSOR": {
                            "value": {
                                "type": "multi_users_select",
                                "selected_users": ["U_PROFESSOR"],
                            }
                        },
                        "approval_role__ADMIN_STAFF": {
                            "value": {
                                "type": "multi_users_select",
                                "selected_users": ["U_ADMIN_STAFF"],
                            }
                        },
                    }
                },
            },
        },
        slack_client,
    )

    assert len(acknowledgements) == 1
    response = acknowledgements[0]
    assert response["response_action"] == "update"
    assert response["view"]["callback_id"] == "loading"
    assert slack_client.opened_views["V_APPROVAL"]["callback_id"] == "configuration_notice"
    assert t("rule_saved") in str(slack_client.opened_views["V_APPROVAL"])
    saved = await LedgerRepository(slack_client, database).get_rule("department_1", "supplies")
    assert saved.approval_channel_id == "C_APPROVAL"
    assert saved.is_complete


@pytest.mark.asyncio
async def test_expense_journey_survives_changes_and_finishes_all_approval_steps(
    slack_client, database
) -> None:
    """Exercise the user-visible journey, not isolated handler return values."""

    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    await ledger.save_applicant_profile(
        "U_REQUESTER",
        applicant_type=ApplicantType.STUDENT,
        applicant_identifier="202600001",
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        return None

    async def respond(**_kwargs) -> None:
        return None

    def plain(value: str) -> dict:
        return {"value": {"type": "plain_text_input", "value": value}}

    def selected(value: str) -> dict:
        return {
            "value": {
                "type": "static_select",
                "selected_option": {"value": value},
            }
        }

    def expense_values(*, purpose: str) -> dict:
        return {
            "amount": plain("49000"),
            "vendor": plain("Example Store"),
            "payment_date": {"value": {"type": "datepicker", "selected_date": "2026-08-15"}},
            "purpose": plain(purpose),
            "evidence_folder": plain("https://drive.google.com/drive/folders/expense-journey"),
        }

    await handlers.views["expense_context"](
        ack,
        {
            "user": {"id": "U_REQUESTER"},
            "view": {
                "id": "V_CONTEXT",
                "private_metadata": json.dumps(
                    {
                        "profile": {
                            "slack_user_id": "U_REQUESTER",
                            "applicant_type": "STUDENT",
                            "applicant_identifier": "202600001",
                        }
                    }
                ),
                "state": {
                    "values": {
                        "department": selected("department_1"),
                        "budget_level_1": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "department_budget"},
                            }
                        },
                        "budget_level_2": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "academic_development"},
                            }
                        },
                        "budget_level_3": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "supplies"},
                            }
                        },
                    }
                },
            },
        },
        slack_client,
    )
    details = slack_client.opened_views["V_CONTEXT"]
    assert details["callback_id"] == "expense_details"

    await handlers.views["expense_details"](
        ack,
        {
            "user": {"id": "U_REQUESTER", "name": "Requester"},
            "view": {
                "id": "V_CONTEXT",
                "private_metadata": details["private_metadata"],
                "state": {"values": expense_values(purpose="Initial purpose")},
            },
        },
        slack_client,
    )
    request = (await ledger.list_for_applicant("U_REQUESTER"))[0]
    assert request.status == RequestStatus.IN_APPROVAL
    assert request.current_approver_slack_user_ids == ("U_COORDINATOR",)

    await handlers.actions["approve_request"](
        ack,
        {
            "user": {"id": "U_COORDINATOR"},
            "actions": [{"value": request.id}],
        },
        slack_client,
        respond,
    )
    request = await ledger.get_request(request.id)
    assert request.current_approver_slack_user_ids == ("U_PROFESSOR",)

    await handlers.actions["request_changes"](
        ack,
        {
            "trigger_id": "TRIGGER_CHANGES",
            "user": {"id": "U_PROFESSOR"},
            "actions": [{"value": request.id}],
        },
        slack_client,
        respond,
    )
    decision = slack_client.opened_views["V1"]
    await handlers.views["approval_decision"](
        ack,
        {
            "user": {"id": "U_PROFESSOR"},
            "view": {
                "id": "V1",
                "private_metadata": decision["private_metadata"],
                "state": {"values": {"decision_reason": plain("Clarify the purpose")}},
            },
        },
        slack_client,
    )
    request = await ledger.get_request(request.id)
    assert request.status == RequestStatus.CHANGES_REQUESTED
    requester_home = slack_client.published_views["U_REQUESTER"]
    assert "edit_request" in str(requester_home)

    await handlers.actions["edit_request"](
        ack,
        {
            "trigger_id": "TRIGGER_EDIT",
            "user": {"id": "U_REQUESTER"},
            "actions": [{"value": request.id}],
        },
        slack_client,
    )
    edit_view = slack_client.opened_views["V2"]
    assert edit_view["callback_id"] == "expense_edit"
    await handlers.views["expense_edit"](
        ack,
        {
            "user": {"id": "U_REQUESTER"},
            "view": {
                "id": "V2",
                "private_metadata": edit_view["private_metadata"],
                "state": {"values": expense_values(purpose="Clarified purpose")},
            },
        },
        slack_client,
    )
    request = await ledger.get_request(request.id)
    assert request.status == RequestStatus.IN_APPROVAL
    assert request.purpose == "Clarified purpose"
    assert request.current_approver_slack_user_ids == ("U_PROFESSOR",)

    for approver in ("U_PROFESSOR", "U_ADMIN_STAFF"):
        await handlers.actions["approve_request"](
            ack,
            {"user": {"id": approver}, "actions": [{"value": request.id}]},
            slack_client,
            respond,
        )
        request = await ledger.get_request(request.id)

    assert request.status == RequestStatus.COMPLETED
    assert request.current_approver_slack_user_ids == ()


@pytest.mark.asyncio
async def test_assigned_settlement_authorizes_expense_without_direct_requester_role(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    await ledger.save_approval_route(ROOT_ADMIN, "department_1", "supplies", "C_APPROVAL")
    command = CreateSettlementRequestCommand(
        requester_slack_user_id="U_ADMIN_STAFF",
        department_id="department_1",
        budget_node_id="supplies",
        assignee_slack_user_id="U_STUDENT",
        subject="Assigned expense",
        vendor="Vendor",
        amount="1000",
        payment_date=date(2026, 8, 15),
        purpose="Assigned settlement authorization",
        evidence_folder_url="https://drive.google.com/drive/folders/example",
    )
    source = await ledger.create_work_request(
        settlement_created_data(
            command,
            department_by_id(command.department_id),
            category_for_budget_node(command.budget_node_id, command.department_id),
            "C_APPROVAL",
        )
    )
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

    def selected(value: str) -> dict:
        return {
            "value": {
                "type": "static_select",
                "selected_option": {"value": value},
            }
        }

    await handlers.views["expense_context"](
        ack,
        {
            "user": {"id": command.assignee_slack_user_id},
            "view": {
                "id": "V_CONTEXT",
                "private_metadata": json.dumps(
                    {
                        "source_work_request_id": source.id,
                        "profile": {
                            "slack_user_id": command.assignee_slack_user_id,
                            "applicant_type": "STUDENT",
                            "applicant_identifier": "202600001",
                        },
                    }
                ),
                "state": {
                    "values": {
                        "department": selected("department_1"),
                        "budget_level_1": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "department_budget"},
                            }
                        },
                        "budget_level_2": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "academic_development"},
                            }
                        },
                        "budget_level_3": {
                            "budget_node_selected": {
                                "type": "static_select",
                                "selected_option": {"value": "supplies"},
                            }
                        },
                    }
                },
            },
        },
        slack_client,
    )

    assert acknowledgements == [{"response_action": "update", "view": loading_modal()}]
    assert slack_client.opened_views["V_CONTEXT"]["callback_id"] == "expense_details"
