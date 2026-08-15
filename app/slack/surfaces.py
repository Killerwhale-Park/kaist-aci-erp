import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.models.views import View

from app.i18n import t

logger = logging.getLogger(__name__)


def validated_view(view: dict) -> dict:
    """Validate and normalize a raw Block Kit view before it reaches Slack."""

    model = View(**view)
    model.validate_json()
    return model.to_dict()


@dataclass(frozen=True)
class SlackModalFailure:
    operation: str
    callback_id: str
    code: str
    messages: tuple[str, ...]

    @classmethod
    def from_error(
        cls,
        error: SlackApiError,
        *,
        operation: str,
        callback_id: str,
    ) -> "SlackModalFailure":
        response = error.response
        metadata = response.get("response_metadata") or {}
        raw_messages = metadata.get("messages") or ()
        return cls(
            operation=operation,
            callback_id=callback_id,
            code=str(response.get("error") or "unknown_error"),
            messages=tuple(str(message) for message in raw_messages),
        )

    def alert_text(self) -> str:
        location = f"{self.callback_id}/{self.operation}"
        detail = "; ".join(self.messages)
        suffix = f" — {detail}" if detail else ""
        return f"Slack modal failure [{location}]: `{self.code}`{suffix}"


class SlackSurfaces:
    """Own Slack view I/O and preserve actionable API diagnostics."""

    def __init__(
        self,
        client: Any,
        report_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self.client = client
        self.report_alert = report_alert

    async def open_modal(self, trigger_id: str, view: dict, user_id: str):
        normalized = validated_view(view)
        return await self._call(
            "open",
            normalized,
            user_id,
            lambda: self.client.views_open(trigger_id=trigger_id, view=normalized),
        )

    async def push_modal(self, trigger_id: str, view: dict, user_id: str):
        normalized = validated_view(view)
        return await self._call(
            "push",
            normalized,
            user_id,
            lambda: self.client.views_push(trigger_id=trigger_id, view=normalized),
        )

    async def update_modal(
        self,
        view_id: str,
        view: dict,
        user_id: str,
        *,
        view_hash: str | None = None,
    ):
        normalized = validated_view(view)
        arguments = {"view_id": view_id, "view": normalized}
        if view_hash:
            arguments["hash"] = view_hash
        return await self._call(
            "update",
            normalized,
            user_id,
            lambda: self.client.views_update(**arguments),
        )

    async def _call(
        self,
        operation: str,
        view: dict,
        user_id: str,
        call: Callable[[], Awaitable[Any]],
    ):
        try:
            return await call()
        except SlackApiError as error:
            failure = SlackModalFailure.from_error(
                error,
                operation=operation,
                callback_id=str(view.get("callback_id") or "unknown"),
            )
            logger.error("%s", failure.alert_text(), exc_info=True)
            await self._notify_user(user_id, failure)
            try:
                await self.report_alert(failure.alert_text())
            except Exception:
                logger.exception("Failed to publish Slack modal alert")
            return None

    async def _notify_user(self, user_id: str, failure: SlackModalFailure) -> None:
        expired = failure.code in {
            "exchanged_trigger_id",
            "expired_trigger_id",
            "invalid_trigger",
            "invalid_trigger_id",
            "trigger_exchanged",
            "trigger_expired",
        }
        message = t("form_open_expired" if expired else "form_open_error")
        diagnostic = "; ".join(failure.messages) or failure.code
        try:
            await self.client.chat_postMessage(
                channel=user_id,
                text=f"{message}\n`{diagnostic}`",
            )
        except SlackApiError:
            logger.exception("Failed to send modal error to %s", user_id)
