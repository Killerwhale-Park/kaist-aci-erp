from __future__ import annotations

import asyncio
from typing import Any

from app.config.budgets import LEGACY_BUDGET_IDS
from app.config.settings import Settings
from app.domain.catalog import categories, default_rule
from app.domain.enums import WorkRequestStatus
from app.domain.models import ApprovalRule, ApprovalRuleStep, ExpenseRequest, WorkRequest
from app.domain.work_requests import (
    WORK_REQUEST_COMPLETED,
    WORK_REQUEST_CREATED,
    replay_work_events,
    work_request_from_created,
    work_request_summary,
)
from app.domain.workflow import (
    REQUEST_CREATED,
    replay_events,
    request_from_created,
    request_summary,
    validate_transition,
)
from app.exceptions import (
    ApprovalPermissionError,
    ConfigurationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from app.ledger.codec import decode_chunks, encode_chunks, event_record

EXPENSE_ROOT = "expense_record"
EXPENSE_EVENT_CHUNK = "expense_event_chunk"
WORK_REQUEST_ROOT = "work_request_record"
WORK_REQUEST_EVENT_CHUNK = "work_request_event_chunk"
CONFIG_ROOT = "configuration_record"
CONFIG_CHUNK = "configuration_chunk"
RULE_SAVED = "RULE_SAVED"
SYSTEM_ADMINS_SAVED = "SYSTEM_ADMINS_SAVED"
CHANNEL_ID = "_channel_id"


class SlackLedgerRepository:
    def __init__(self, client, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._channel_cache: list[str] | None = None
        self._history_cache: dict[str, list[dict]] = {}

    async def _channel_ids(self) -> list[str]:
        if self._channel_cache is not None:
            return self._channel_cache

        channel_ids: list[str] = []
        cursor: str | None = None
        while True:
            response = await self.client.conversations_list(
                types="private_channel",
                exclude_archived=True,
                limit=200,
                **({"cursor": cursor} if cursor else {}),
            )
            channel_ids.extend(
                channel["id"]
                for channel in response.get("channels", [])
                if channel.get("is_member", True)
            )
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                self._channel_cache = sorted(set(channel_ids))
                return self._channel_cache

    async def _history(self, channel_id: str) -> list[dict]:
        if channel_id in self._history_cache:
            return self._history_cache[channel_id]

        messages: list[dict] = []
        cursor: str | None = None
        while True:
            response = await self.client.conversations_history(
                channel=channel_id,
                limit=999,
                include_all_metadata=True,
                **({"cursor": cursor} if cursor else {}),
            )
            messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                self._history_cache[channel_id] = messages
                return messages

    async def _thread(self, channel_id: str, root_ts: str) -> list[dict]:
        messages: list[dict] = []
        cursor: str | None = None
        while True:
            response = await self.client.conversations_replies(
                channel=channel_id,
                ts=root_ts,
                limit=999,
                include_all_metadata=True,
                **({"cursor": cursor} if cursor else {}),
            )
            messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                return messages

    def _invalidate_history(self, channel_id: str) -> None:
        self._history_cache.pop(channel_id, None)

    @staticmethod
    def _metadata(message: dict) -> tuple[str | None, dict[str, Any]]:
        metadata = message.get("metadata") or {}
        return metadata.get("event_type"), metadata.get("event_payload") or {}

    async def _roots(self, event_type: str) -> list[dict]:
        roots: list[dict] = []
        channel_ids = await self._channel_ids()
        histories = await asyncio.gather(*(self._history(channel_id) for channel_id in channel_ids))
        for channel_id, messages in zip(channel_ids, histories, strict=True):
            for message in messages:
                message_event_type, _ = self._metadata(message)
                if message_event_type == event_type and not message.get("thread_ts"):
                    roots.append({**message, CHANNEL_ID: channel_id})
        return sorted(roots, key=lambda item: item.get("ts", ""), reverse=True)

    async def _expense_roots(self) -> list[dict]:
        return await self._roots(EXPENSE_ROOT)

    async def _work_request_roots(self) -> list[dict]:
        return await self._roots(WORK_REQUEST_ROOT)

    async def _find_expense_root(self, request_id: str) -> dict:
        for message in await self._expense_roots():
            _, summary = self._metadata(message)
            if summary.get("request_id") == request_id:
                return message
        raise EntityNotFoundError(f"Expense request not found: {request_id}")

    async def _find_work_request_root(self, request_id: str) -> dict:
        for message in await self._work_request_roots():
            _, summary = self._metadata(message)
            if summary.get("work_request_id") == request_id:
                return message
        raise EntityNotFoundError(f"Work request not found: {request_id}")

    async def _append_chunks(
        self,
        *,
        channel_id: str,
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
                channel=channel_id,
                thread_ts=root_ts,
                text=text,
                metadata={"event_type": metadata_event_type, "event_payload": payload},
                unfurl_links=False,
                unfurl_media=False,
            )

    async def create_request(self, created_data: dict[str, Any]) -> ExpenseRequest:
        provisional = request_from_created(created_data)
        channel_id = provisional.approval_channel_id
        if channel_id not in await self._channel_ids():
            raise ConfigurationError("The app is not a member of the configured channel")

        response = await self.client.chat_postMessage(
            channel=channel_id,
            text=f"Expense request {provisional.reference_number}",
            metadata={"event_type": EXPENSE_ROOT, "event_payload": request_summary(provisional)},
            unfurl_links=False,
            unfurl_media=False,
        )
        self._invalidate_history(channel_id)
        root_ts = response["ts"]
        await self._append_event_to_root(
            channel_id,
            root_ts,
            REQUEST_CREATED,
            provisional.applicant_slack_user_id,
            created_data,
        )
        return await self._load_from_root({"ts": root_ts, CHANNEL_ID: channel_id})

    async def channel_is_available(self, channel_id: str) -> bool:
        return channel_id in await self._channel_ids()

    async def create_work_request(self, created_data: dict[str, Any]) -> WorkRequest:
        provisional = work_request_from_created(created_data)
        channel_id = provisional.channel_id
        if not await self.channel_is_available(channel_id):
            raise ConfigurationError("The app is not a member of the selected channel")

        response = await self.client.chat_postMessage(
            channel=channel_id,
            text=f"Work request {provisional.reference_number}",
            metadata={
                "event_type": WORK_REQUEST_ROOT,
                "event_payload": work_request_summary(provisional),
            },
            unfurl_links=False,
            unfurl_media=False,
        )
        self._invalidate_history(channel_id)
        root_ts = response["ts"]
        await self._append_chunks(
            channel_id=channel_id,
            root_ts=root_ts,
            metadata_event_type=WORK_REQUEST_EVENT_CHUNK,
            record_type="work_request_event",
            data=event_record(
                WORK_REQUEST_CREATED, provisional.requester_slack_user_id, created_data
            ),
            audit_text=f"{WORK_REQUEST_CREATED} by <@{provisional.requester_slack_user_id}>",
        )
        return await self._load_work_request_from_root({"ts": root_ts, CHANNEL_ID: channel_id})

    async def _append_event_to_root(
        self,
        channel_id: str,
        root_ts: str,
        kind: str,
        actor: str,
        data: dict[str, Any],
    ) -> None:
        await self._append_chunks(
            channel_id=channel_id,
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
        await self._append_event_to_root(root[CHANNEL_ID], root["ts"], kind, actor, data or {})
        updated = await self._load_from_root(root)
        await self._update_summary(root, updated)
        return updated

    async def _update_summary(self, root: dict, request: ExpenseRequest) -> None:
        arguments: dict[str, Any] = {
            "channel": root[CHANNEL_ID],
            "ts": root["ts"],
            "text": root.get("text") or f"Expense request {request.reference_number}",
            "metadata": {
                "event_type": EXPENSE_ROOT,
                "event_payload": request_summary(request),
            },
        }
        if root.get("blocks") is not None:
            arguments["blocks"] = root["blocks"]
        await self.client.chat_update(**arguments)
        self._invalidate_history(root[CHANNEL_ID])

    async def _load_from_root(self, root: dict) -> ExpenseRequest:
        records = decode_chunks(
            await self._thread(root[CHANNEL_ID], root["ts"]),
            event_type=EXPENSE_EVENT_CHUNK,
        )
        events = [
            {"ts": record["ts"], **record["data"]}
            for record in records
            if record["record_type"] == "expense_event"
        ]
        return replay_events(events, message_ts=root["ts"])

    async def _load_work_request_from_root(self, root: dict) -> WorkRequest:
        records = decode_chunks(
            await self._thread(root[CHANNEL_ID], root["ts"]),
            event_type=WORK_REQUEST_EVENT_CHUNK,
        )
        events = [
            {"ts": record["ts"], **record["data"]}
            for record in records
            if record["record_type"] == "work_request_event"
        ]
        return replay_work_events(events, message_ts=root["ts"])

    async def get_request(self, request_id: str) -> ExpenseRequest:
        return await self._load_from_root(await self._find_expense_root(request_id))

    async def get_work_request(self, request_id: str) -> WorkRequest:
        return await self._load_work_request_from_root(
            await self._find_work_request_root(request_id)
        )

    async def complete_work_request(self, request_id: str, actor: str) -> WorkRequest:
        root = await self._find_work_request_root(request_id)
        current = await self._load_work_request_from_root(root)
        allowed = {
            current.requester_slack_user_id,
            current.assignee_slack_user_id,
            *(await self.system_admin_ids()),
        }
        if actor not in allowed:
            raise ApprovalPermissionError("Only a participant can complete this work request")
        if current.status != WorkRequestStatus.OPEN:
            raise InvalidStateTransitionError("Work request is already completed")
        await self._append_chunks(
            channel_id=root[CHANNEL_ID],
            root_ts=root["ts"],
            metadata_event_type=WORK_REQUEST_EVENT_CHUNK,
            record_type="work_request_event",
            data=event_record(WORK_REQUEST_COMPLETED, actor, {}),
            audit_text=f"{WORK_REQUEST_COMPLETED} by <@{actor}>",
        )
        updated = await self._load_work_request_from_root(root)
        await self.client.chat_update(
            channel=updated.channel_id,
            ts=updated.message_ts,
            text=root.get("text") or f"Work request {updated.reference_number}",
            metadata={
                "event_type": WORK_REQUEST_ROOT,
                "event_payload": work_request_summary(updated),
            },
        )
        self._invalidate_history(updated.channel_id)
        return updated

    async def update_work_request_view(
        self, request: WorkRequest, *, text: str, blocks: list[dict]
    ) -> None:
        await self.client.chat_update(
            channel=request.channel_id,
            ts=request.message_ts,
            text=text,
            blocks=blocks,
            metadata={
                "event_type": WORK_REQUEST_ROOT,
                "event_payload": work_request_summary(request),
            },
        )
        self._invalidate_history(request.channel_id)

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

    async def update_request_view(
        self, request: ExpenseRequest, *, text: str, blocks: list[dict]
    ) -> None:
        await self.client.chat_update(
            channel=request.approval_channel_id,
            ts=request.message_ts,
            text=text,
            blocks=blocks,
            metadata={"event_type": EXPENSE_ROOT, "event_payload": request_summary(request)},
        )
        self._invalidate_history(request.approval_channel_id)

    async def _configuration_roots(self, configuration_type: str, key: str) -> list[dict]:
        matches = []
        for message in await self._roots(CONFIG_ROOT):
            _, payload = self._metadata(message)
            if (
                payload.get("configuration_type") == configuration_type
                and payload.get("key") == key
            ):
                matches.append(message)
        return matches

    async def _configuration_data(self, root: dict, record_type: str) -> dict[str, Any]:
        records = decode_chunks(
            await self._thread(root[CHANNEL_ID], root["ts"]), event_type=CONFIG_CHUNK
        )
        record = next((item for item in records if item["record_type"] == record_type), None)
        if record is None:
            raise ConfigurationError("Configuration record is incomplete")
        return record["data"]

    async def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        key = f"{department_id}:{category_id}"
        roots = await self._configuration_roots("approval_rule", key)
        for root in roots:
            try:
                data = await self._configuration_data(root, RULE_SAVED)
            except ConfigurationError:
                continue
            return ApprovalRule(
                department_id=data["department_id"],
                budget_program_id=LEGACY_BUDGET_IDS.get(
                    data["budget_program_id"], data["budget_program_id"]
                ),
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

        rule = default_rule(department_id, category_id)
        if rule is None:
            raise EntityNotFoundError("Approval rule not found")
        return rule

    async def save_rule(self, actor: str, rule: ApprovalRule) -> ApprovalRule:
        await self.assert_system_admin(actor)
        if not rule.approval_channel_id:
            raise ConfigurationError("Approval channel is required")
        if rule.approval_channel_id not in await self._channel_ids():
            raise ConfigurationError("The app is not a member of the approval channel")

        admins = await self.system_admin_ids()
        previous = await self.get_rule(rule.department_id, rule.category_id)
        stored = ApprovalRule(
            department_id=rule.department_id,
            budget_program_id=LEGACY_BUDGET_IDS.get(rule.budget_program_id, rule.budget_program_id),
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
            channel=stored.approval_channel_id,
            text=f"Approval workflow updated: {key} (version {stored.version})",
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
        self._invalidate_history(stored.approval_channel_id)
        await self._append_chunks(
            channel_id=stored.approval_channel_id,
            root_ts=response["ts"],
            metadata_event_type=CONFIG_CHUNK,
            record_type=RULE_SAVED,
            data=data,
            audit_text=f"Approval rule saved by <@{actor}>",
        )
        await self._write_admin_record(stored.approval_channel_id, actor, admins)
        return stored

    async def system_admin_ids(self) -> set[str]:
        roots = await self._configuration_roots("system_admins", "workspace")
        for root in roots:
            try:
                data = await self._configuration_data(root, SYSTEM_ADMINS_SAVED)
            except ConfigurationError:
                continue
            return set(data["slack_user_ids"])
        return self.settings.bootstrap_system_admin_ids

    async def assert_system_admin(self, actor: str) -> None:
        if actor not in await self.system_admin_ids():
            raise ApprovalPermissionError("System administrator role required")

    async def settlement_assigner_ids(self) -> set[str]:
        allowed = set(await self.system_admin_ids())
        valid_category_ids = {item.id for item in categories()}
        seen: set[str] = set()
        for root in await self._roots(CONFIG_ROOT):
            _, payload = self._metadata(root)
            if payload.get("configuration_type") != "approval_rule":
                continue
            key = str(payload.get("key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                data = await self._configuration_data(root, RULE_SAVED)
            except ConfigurationError:
                continue
            if data.get("category_id") not in valid_category_ids:
                continue
            for step in data.get("steps", []):
                allowed.update(step.get("approver_slack_user_ids", []))
        return allowed

    async def assert_can_assign_settlement(self, actor: str) -> None:
        if actor not in await self.settlement_assigner_ids():
            raise ApprovalPermissionError("Approval or administrator role required")

    async def _write_admin_record(
        self, channel_id: str, actor: str, slack_user_ids: set[str]
    ) -> None:
        response = await self.client.chat_postMessage(
            channel=channel_id,
            text="System administrators updated",
            metadata={
                "event_type": CONFIG_ROOT,
                "event_payload": {
                    "version": 1,
                    "configuration_type": "system_admins",
                    "key": "workspace",
                },
            },
        )
        self._invalidate_history(channel_id)
        await self._append_chunks(
            channel_id=channel_id,
            root_ts=response["ts"],
            metadata_event_type=CONFIG_CHUNK,
            record_type=SYSTEM_ADMINS_SAVED,
            data={"slack_user_ids": sorted(slack_user_ids)},
            audit_text=f"System administrators saved by <@{actor}>",
        )

    async def replace_system_admins(self, actor: str, slack_user_ids: list[str]) -> None:
        await self.assert_system_admin(actor)
        selected = set(slack_user_ids)
        if not selected:
            raise ConfigurationError("At least one administrator is required")
        channel_ids = await self._channel_ids()
        if not channel_ids:
            raise ConfigurationError("The app must join at least one private channel")
        for channel_id in channel_ids:
            await self._write_admin_record(channel_id, actor, selected)
