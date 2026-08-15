import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse

from app.slack.modals import loading_modal
from app.slack.surfaces import SlackModalFailure, SlackSurfaces


def slack_error(data: dict) -> SlackApiError:
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/views.open",
        req_args={},
        data=data,
        headers={},
        status_code=200,
    )
    return SlackApiError("Slack rejected the view", response)


def test_modal_failure_preserves_callback_operation_and_messages() -> None:
    failure = SlackModalFailure.from_error(
        slack_error(
            {
                "ok": False,
                "error": "invalid_arguments",
                "response_metadata": {"messages": ["[ERROR] invalid field at blocks[2].element"]},
            }
        ),
        operation="open",
        callback_id="settlement_request_create",
    )

    assert failure.code == "invalid_arguments"
    assert failure.messages == ("[ERROR] invalid field at blocks[2].element",)
    assert "settlement_request_create/open" in failure.alert_text()
    assert "blocks[2].element" in failure.alert_text()


@pytest.mark.asyncio
async def test_surface_reports_actionable_slack_diagnostics() -> None:
    alerts: list[str] = []
    direct_messages: list[dict] = []

    class RejectingClient:
        async def views_open(self, **_kwargs):
            raise slack_error(
                {
                    "ok": False,
                    "error": "invalid_arguments",
                    "response_metadata": {"messages": ["invalid conversations_select filter"]},
                }
            )

        async def chat_postMessage(self, **kwargs):
            direct_messages.append(kwargs)

    async def report_alert(message: str) -> None:
        alerts.append(message)

    result = await SlackSurfaces(RejectingClient(), report_alert).open_modal(
        "trigger",
        {**loading_modal(), "callback_id": "settlement_request_create"},
        "U_USER",
    )

    assert result is None
    assert len(alerts) == 1
    assert "settlement_request_create/open" in alerts[0]
    assert "conversations_select" in alerts[0]
    assert direct_messages[0]["channel"] == "U_USER"
    assert "conversations_select" in direct_messages[0]["text"]
