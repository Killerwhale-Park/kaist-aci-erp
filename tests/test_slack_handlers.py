import json
from collections.abc import Callable
from datetime import date

import pytest

from app.domain.catalog import department_by_id
from app.domain.work_requests import settlement_created_data
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
async def test_direct_expense_form_opens_without_settlement_or_approval_route(
    slack_client, database
) -> None:
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

    assert slack_client.call_order[:2] == ["ack", "views_open"]
    assert slack_client.opened_views["V1"]["callback_id"] == "expense_context"


@pytest.mark.asyncio
async def test_received_settlement_is_listed_and_can_start_after_loading_view(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    command = settlement_command()
    request = await ledger.create_work_request(
        settlement_created_data(command, department_by_id(command.department_id))
    )
    handlers = registered_handlers(database)

    async def ack(**_kwargs) -> None:
        slack_client.call_order.append("ack")

    await handlers.actions["refresh_home"](
        ack,
        {"user": {"id": command.assignee_slack_user_id}},
        slack_client,
    )
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
    assert slack_client.opened_views["V1"]["callback_id"] == "expense_context"
    assert "source_work_request_id" in slack_client.opened_views["V1"]["private_metadata"]


@pytest.mark.asyncio
async def test_related_user_can_view_work_request_without_admin_lookup(
    slack_client, database, monkeypatch
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    command = settlement_command()
    request = await ledger.create_work_request(
        settlement_created_data(command, department_by_id(command.department_id))
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
async def test_purchase_submission_persists_and_refreshes_sender_and_receiver_homes(
    slack_client, database
) -> None:
    ledger = LedgerRepository(slack_client, database)
    await register_test_channels(ledger)
    await ledger.replace_role_assignments(ROOT_ADMIN, role_configuration())
    handlers = registered_handlers(database)
    acknowledgements: list[dict] = []

    async def ack(**kwargs) -> None:
        acknowledgements.append(kwargs)

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


@pytest.mark.asyncio
async def test_access_role_configuration_pushes_loading_before_database_read(
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
        assignee_slack_user_id="U_STUDENT",
        channel_id="C_APPROVAL",
        subject="Assigned expense",
        vendor="Vendor",
        amount="1000",
        payment_date=date(2026, 8, 15),
        purpose="Assigned settlement authorization",
        evidence_folder_url="https://drive.google.com/drive/folders/example",
    )
    source = await ledger.create_work_request(
        settlement_created_data(command, department_by_id(command.department_id))
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
                "private_metadata": json.dumps({"source_work_request_id": source.id}),
                "state": {
                    "values": {
                        "department": selected("department_1"),
                        "applicant_type": {
                            "applicant_type_changed": {
                                "type": "static_select",
                                "selected_option": {"value": "STUDENT"},
                            }
                        },
                        "student_number": {
                            "value": {
                                "type": "plain_text_input",
                                "value": "202600001",
                            }
                        },
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
