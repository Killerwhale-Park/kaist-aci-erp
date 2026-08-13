from __future__ import annotations

import logging
from typing import Any

from app.config.settings import Settings
from app.domain.catalog import default_rule
from app.domain.models import ApprovalRule, ApprovalRuleStep, ExpenseRequest
from app.domain.workflow import (
    MIRROR_LINKED,
    REQUEST_CREATED,
    replay_events,
    request_from_created,
    request_summary,
    validate_transition,
)
from app.exceptions import ApprovalPermissionError, ConfigurationError, EntityNotFoundError
from app.ledger.codec import decode_chunks, encode_chunks, event_record

logger = logging.getLogger(__name__)

EXPENSE_ROOT = "expense_record"
EXPENSE_EVENT_CHUNK = "expense_event_chunk"
CONFIG_ROOT = "configuration_record"
CONFIG_CHUNK = "configuration_chunk"
RULE_SAVED = "RULE_SAVED"
SYSTEM_ADMINS_SAVED = "SYSTEM_ADMINS_SAVED"


class SlackLedgerRepository:
    def __init__(self, client, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.channel_id = settings.slack_ledger_channel_id
        if not self.channel_id:
            raise ConfigurationError("SLACK_LEDGER_CHANNEL_ID is required")

    async def _history(self) -> list[dict]:
        messages: list[dict] = []
        cursor: str | None = None
        while True:
            response = await self.client.conversations_history(
                channel=self.channel_id,
                limit=1000,
                include_all_metadata=True,
                **({"cursor": cursor} if cursor else {}),
            )
            messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                return messages

    async def _thread(self, root_ts: str) -> list[dict]:
        messages: list[dict] = []
        cursor: str | None = None
        while True:
            response = await self.client.conversations_replies(
                channel=self.channel_id,
                ts=root_ts,
                limit=1000,
                include_all_metadata=True,
                **({"cursor": cursor} if cursor else {}),
            )
            messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                return messages

    @staticmethod
    def _metadata(message: dict) -> tuple[str | None, dict[str, Any]]:
        metadata = message.get("metadata") or {}
        return metadata.get("event_type"), metadata.get("event_payload") or {}

    async def _expense_roots(self) -> list[dict]:
        roots = []
        for message in await self._history():
            event_type, _ = self._metadata(message)
            if event_type == EXPENSE_ROOT and not message.get("thread_ts"):
                roots.append(message)
        return sorted(roots, key=lambda item: item.get("ts", ""), reverse=True)

    async def _find_expense_root(self, request_id: str) -> dict:
        for message in await self._expense_roots():
            _, summary = self._metadata(message)
            if summary.get("request_id") == request_id:
                return message
        raise EntityNotFoundError(f"Expense request not found: {request_id}")

    async def _append_chunks(
        self,
        *,
        root_ts: str,
        metadata_event_type: str,
        record_type: str,
        data: dict[str, Any],
        audit_text: str,
    ) -> None:
        chunks = encode_chunks(record_type=record_type, data=data)
        for index, payload in enumerate(chunks):
            text = audit_text if index == 0 else f"{audit_text} (continued {index + 1})"
            await self.client.chat_postMessage(
                channel=self.channel_id,
                thread_ts=root_ts,
                text=text,
                metadata={"event_type": metadata_event_type, "event_payload": payload},
                unfurl_links=False,
                unfurl_media=False,
            )

    async def create_request(self, created_data: dict[str, Any]) -> ExpenseRequest:
        provisional = request_from_created(created_data)
        response = await self.client.chat_postMessage(
            channel=self.channel_id,
            text=f"Expense ledger record {provisional.reference_number}",
            metadata={"event_type": EXPENSE_ROOT, "event_payload": request_summary(provisional)},
            unfurl_links=False,
            unfurl_media=False,
        )
        root_ts = response["ts"]
        await self._append_event_to_root(
            root_ts,
            REQUEST_CREATED,
            provisional.applicant_slack_user_id,
            created_data,
        )
        return await self._load_from_root({"ts": root_ts})

    async def _append_event_to_root(
        self,
        root_ts: str,
        kind: str,
        actor: str,
        data: dict[str, Any],
    ) -> None:
        await self._append_chunks(
            root_ts=root_ts,
            metadata_event_type=EXPENSE_EVENT_CHUNK,
            record_type="expense_event",
            data=event_record(kind, actor, data),
            audit_text=f"{kind} by <@{actor}>" if actor else kind,
        )

    async def append_event(
        self, request_id: str, kind: str, actor: str, data: dict[str, Any] | None = None
    ) -> ExpenseRequest:
        root = await self._find_expense_root(request_id)
        current = await self._load_from_root(root)
        validate_transition(current, kind, actor, data or {})
        await self._append_event_to_root(root["ts"], kind, actor, data or {})
        updated = await self._load_from_root(root)
        await self._update_summary_cache(root, updated)
        return updated

    async def _update_summary_cache(self, root: dict, request: ExpenseRequest) -> None:
        arguments: dict[str, Any] = {
            "channel": self.channel_id,
            "ts": root["ts"],
            "text": root.get("text") or f"Expense ledger record {request.reference_number}",
            "metadata": {
                "event_type": EXPENSE_ROOT,
                "event_payload": request_summary(request),
            },
        }
        if root.get("blocks") is not None:
            arguments["blocks"] = root["blocks"]
        await self.client.chat_update(**arguments)

    async def link_approval_message(
        self, request_id: str, approval_message_ts: str
    ) -> ExpenseRequest:
        return await self.append_event(
            request_id,
            MIRROR_LINKED,
            "",
            {"approval_message_ts": approval_message_ts},
        )

    async def _load_from_root(self, root: dict) -> ExpenseRequest:
        root_ts = root["ts"]
        records = decode_chunks(await self._thread(root_ts), event_type=EXPENSE_EVENT_CHUNK)
        events = [
            {"ts": record["ts"], **record["data"]}
            for record in records
            if record["record_type"] == "expense_event"
        ]
        return replay_events(events, ledger_ts=root_ts)

    async def get_request(self, request_id: str) -> ExpenseRequest:
        return await self._load_from_root(await self._find_expense_root(request_id))

    async def list_for_applicant(self, slack_user_id: str) -> list[ExpenseRequest]:
        matches = []
        for root in await self._expense_roots():
            _, summary = self._metadata(root)
            if summary.get("applicant_slack_user_id") == slack_user_id:
                matches.append(await self._load_from_root(root))
        return matches

    async def list_pending_for_actor(self, slack_user_id: str) -> list[ExpenseRequest]:
        matches = []
        for root in await self._expense_roots():
            _, summary = self._metadata(root)
            if slack_user_id in summary.get("current_approver_slack_user_ids", []):
                request = await self._load_from_root(root)
                if request.status.value == "IN_APPROVAL":
                    matches.append(request)
        return matches

    async def update_ledger_view(
        self, request: ExpenseRequest, *, text: str, blocks: list[dict]
    ) -> None:
        await self.client.chat_update(
            channel=self.channel_id,
            ts=request.ledger_message_ts,
            text=text,
            blocks=blocks,
            metadata={"event_type": EXPENSE_ROOT, "event_payload": request_summary(request)},
        )

    async def _configuration_roots(self, configuration_type: str, key: str) -> list[dict]:
        matches = []
        for message in await self._history():
            event_type, payload = self._metadata(message)
            if (
                event_type == CONFIG_ROOT
                and payload.get("configuration_type") == configuration_type
                and payload.get("key") == key
                and not message.get("thread_ts")
            ):
                matches.append(message)
        return sorted(matches, key=lambda item: item.get("ts", ""), reverse=True)

    async def _configuration_data(self, root: dict, record_type: str) -> dict[str, Any]:
        records = decode_chunks(await self._thread(root["ts"]), event_type=CONFIG_CHUNK)
        record = next((item for item in records if item["record_type"] == record_type), None)
        if record is None:
            raise ConfigurationError("Configuration record is incomplete")
        return record["data"]

    async def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        key = f"{department_id}:{category_id}"
        roots = await self._configuration_roots("approval_rule", key)
        if not roots:
            rule = default_rule(department_id, category_id)
            if rule is None:
                raise EntityNotFoundError("Approval rule not found")
            return rule
        data = await self._configuration_data(roots[0], RULE_SAVED)
        return ApprovalRule(
            department_id=data["department_id"],
            budget_program_id=data["budget_program_id"],
            category_id=data["category_id"],
            approval_channel_id=data["approval_channel_id"],
            steps=tuple(
                ApprovalRuleStep(
                    name_en=item["name_en"],
                    name_ko=item["name_ko"],
                    approver_slack_user_ids=tuple(item["approver_slack_user_ids"]),
                )
                for item in data["steps"]
            ),
            version=int(data["version"]),
        )

    async def save_rule(self, actor: str, rule: ApprovalRule) -> ApprovalRule:
        await self.assert_system_admin(actor)
        previous = await self.get_rule(rule.department_id, rule.category_id)
        stored = ApprovalRule(
            department_id=rule.department_id,
            budget_program_id=rule.budget_program_id,
            category_id=rule.category_id,
            approval_channel_id=rule.approval_channel_id,
            steps=rule.steps,
            version=previous.version + 1,
        )
        data = {
            "department_id": stored.department_id,
            "budget_program_id": stored.budget_program_id,
            "category_id": stored.category_id,
            "approval_channel_id": stored.approval_channel_id,
            "version": stored.version,
            "steps": [
                {
                    "name_en": step.name_en,
                    "name_ko": step.name_ko,
                    "approver_slack_user_ids": list(step.approver_slack_user_ids),
                }
                for step in stored.steps
            ],
        }
        key = f"{stored.department_id}:{stored.category_id}"
        response = await self.client.chat_postMessage(
            channel=self.channel_id,
            text=f"Approval rule {key} version {stored.version}",
            metadata={
                "event_type": CONFIG_ROOT,
                "event_payload": {
                    "version": 1,
                    "configuration_type": "approval_rule",
                    "key": key,
                    "configuration_version": stored.version,
                },
            },
        )
        await self._append_chunks(
            root_ts=response["ts"],
            metadata_event_type=CONFIG_CHUNK,
            record_type=RULE_SAVED,
            data=data,
            audit_text=f"Approval rule saved by <@{actor}>",
        )
        return stored

    async def system_admin_ids(self) -> set[str]:
        roots = await self._configuration_roots("system_admins", "workspace")
        if not roots:
            return self.settings.bootstrap_system_admin_ids
        data = await self._configuration_data(roots[0], SYSTEM_ADMINS_SAVED)
        return set(data["slack_user_ids"])

    async def assert_system_admin(self, actor: str) -> None:
        if actor not in await self.system_admin_ids():
            raise ApprovalPermissionError("System administrator role required")

    async def replace_system_admins(self, actor: str, slack_user_ids: list[str]) -> None:
        await self.assert_system_admin(actor)
        selected = sorted(set(slack_user_ids))
        if not selected:
            raise ConfigurationError("At least one administrator is required")
        response = await self.client.chat_postMessage(
            channel=self.channel_id,
            text="System administrator configuration",
            metadata={
                "event_type": CONFIG_ROOT,
                "event_payload": {
                    "version": 1,
                    "configuration_type": "system_admins",
                    "key": "workspace",
                },
            },
        )
        await self._append_chunks(
            root_ts=response["ts"],
            metadata_event_type=CONFIG_CHUNK,
            record_type=SYSTEM_ADMINS_SAVED,
            data={"slack_user_ids": selected},
            audit_text=f"System administrators saved by <@{actor}>",
        )
