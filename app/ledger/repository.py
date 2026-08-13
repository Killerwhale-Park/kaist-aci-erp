from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

from app.config.roles import (
    ASSIGN_SETTLEMENT,
    MANAGE_CONFIGURATION,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    default_role_assignments,
    empty_role_set,
    role_ids,
    roles_with_capability,
)
from app.config.settings import Settings
from app.domain.catalog import category_by_id, workflow_for_budget_node
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
SYSTEM_CONFIG_ROOT = "system_configuration_snapshot"
SYSTEM_CONFIG_CHUNK = "system_configuration_chunk"
SYSTEM_CONFIG_SNAPSHOT = "SYSTEM_CONFIGURATION_SNAPSHOT"
RULE_SAVED = "RULE_SAVED"
ROLE_ASSIGNMENTS_SAVED = "ROLE_ASSIGNMENTS_SAVED"
CHANNEL_ID = "_channel_id"
CACHE_TTL_SECONDS = 30.0

_SHARED_CHANNEL_CACHE: dict[int, tuple[float, list[str]]] = {}
_SHARED_HISTORY_CACHE: dict[tuple[int, str], tuple[float, list[dict]]] = {}
_SHARED_CONFIG_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_SHARED_MEMBER_CACHE: dict[tuple[int, str], tuple[float, set[str]]] = {}
_SHARED_CHANNEL_INFO_CACHE: dict[tuple[int, str], tuple[float, bool]] = {}
_SHARED_ALERT_CACHE: dict[tuple[int, str], float] = {}


class SlackLedgerRepository:
    def __init__(self, client, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        token = getattr(client, "token", None)
        self._shared_cache_key = hash(token) if token else id(client)
        self._channel_cache: list[str] | None = None
        self._history_cache: dict[str, list[dict]] = {}
        self._resolved_system_channel_id: str | None = None

    @staticmethod
    def _cache_is_fresh(stored_at: float) -> bool:
        return time.monotonic() - stored_at < CACHE_TTL_SECONDS

    async def _channel_ids(self) -> list[str]:
        if self._channel_cache is not None:
            return self._channel_cache
        shared = _SHARED_CHANNEL_CACHE.get(self._shared_cache_key)
        if shared and self._cache_is_fresh(shared[0]):
            self._channel_cache = list(shared[1])
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
                _SHARED_CHANNEL_CACHE[self._shared_cache_key] = (
                    time.monotonic(),
                    list(self._channel_cache),
                )
                return self._channel_cache

    async def _history(self, channel_id: str) -> list[dict]:
        if channel_id in self._history_cache:
            return self._history_cache[channel_id]
        shared_key = (self._shared_cache_key, channel_id)
        shared = _SHARED_HISTORY_CACHE.get(shared_key)
        if shared and self._cache_is_fresh(shared[0]):
            self._history_cache[channel_id] = list(shared[1])
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
                _SHARED_HISTORY_CACHE[shared_key] = (time.monotonic(), list(messages))
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
        _SHARED_HISTORY_CACHE.pop((self._shared_cache_key, channel_id), None)

    @staticmethod
    def _metadata(message: dict) -> tuple[str | None, dict[str, Any]]:
        metadata = message.get("metadata") or {}
        return metadata.get("event_type"), metadata.get("event_payload") or {}

    async def _roots(self, event_type: str, channel_ids: list[str] | None = None) -> list[dict]:
        roots: list[dict] = []
        channel_ids = channel_ids if channel_ids is not None else await self._channel_ids()
        histories = await asyncio.gather(*(self._history(channel_id) for channel_id in channel_ids))
        for channel_id, messages in zip(channel_ids, histories, strict=True):
            for message in messages:
                message_event_type, _ = self._metadata(message)
                if message_event_type == event_type and not message.get("thread_ts"):
                    roots.append({**message, CHANNEL_ID: channel_id})
        return sorted(roots, key=lambda item: item.get("ts", ""), reverse=True)

    async def _expense_roots(self) -> list[dict]:
        return await self._roots(EXPENSE_ROOT, await self.registered_operation_channel_ids())

    async def _work_request_roots(self) -> list[dict]:
        return await self._roots(WORK_REQUEST_ROOT, await self.registered_operation_channel_ids())

    async def _root_from_locator(self, locator: str, event_type: str) -> dict | None:
        parts = locator.split("|", 2)
        if len(parts) != 3:
            return None
        channel_id, message_ts, record_id = parts
        if not channel_id or not message_ts or not record_id:
            return None
        response = await self.client.conversations_history(
            channel=channel_id,
            oldest=message_ts,
            latest=message_ts,
            inclusive=True,
            limit=1,
            include_all_metadata=True,
        )
        for message in response.get("messages", []):
            actual_type, summary = self._metadata(message)
            summary_id = summary.get("request_id") or summary.get("work_request_id")
            if actual_type == event_type and summary_id == record_id:
                return {**message, CHANNEL_ID: channel_id}
        return None

    async def _find_expense_root(self, request_id: str) -> dict:
        located = await self._root_from_locator(request_id, EXPENSE_ROOT)
        if located:
            return located
        actual_request_id = request_id.rsplit("|", 1)[-1]
        for message in await self._expense_roots():
            _, summary = self._metadata(message)
            if summary.get("request_id") == actual_request_id:
                return message
        raise EntityNotFoundError(f"Expense request not found: {actual_request_id}")

    async def _find_work_request_root(self, request_id: str) -> dict:
        located = await self._root_from_locator(request_id, WORK_REQUEST_ROOT)
        if located:
            return located
        actual_request_id = request_id.rsplit("|", 1)[-1]
        for message in await self._work_request_roots():
            _, summary = self._metadata(message)
            if summary.get("work_request_id") == actual_request_id:
                return message
        raise EntityNotFoundError(f"Work request not found: {actual_request_id}")

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
        if not await self.channel_is_available(channel_id):
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
        cache_key = (self._shared_cache_key, channel_id)
        shared = _SHARED_CHANNEL_INFO_CACHE.get(cache_key)
        if shared and self._cache_is_fresh(shared[0]):
            return shared[1]
        try:
            response = await self.client.conversations_info(channel=channel_id)
            channel = response.get("channel") or {}
            available = bool(response.get("ok", True) and channel.get("is_member", True))
        except Exception:
            available = False
        _SHARED_CHANNEL_INFO_CACHE[cache_key] = (time.monotonic(), available)
        return available

    async def channel_member_ids(self, channel_id: str) -> set[str]:
        cache_key = (self._shared_cache_key, channel_id)
        shared = _SHARED_MEMBER_CACHE.get(cache_key)
        if shared and self._cache_is_fresh(shared[0]):
            return set(shared[1])
        members: set[str] = set()
        cursor: str | None = None
        while True:
            response = await self.client.conversations_members(
                channel=channel_id,
                limit=200,
                **({"cursor": cursor} if cursor else {}),
            )
            members.update(response.get("members", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                _SHARED_MEMBER_CACHE[cache_key] = (time.monotonic(), set(members))
                return members

    async def assert_channel_member(self, actor: str, channel_id: str) -> None:
        if actor not in await self.channel_member_ids(channel_id):
            raise ApprovalPermissionError("The actor is not a member of the operating channel")

    async def create_work_request(self, created_data: dict[str, Any]) -> WorkRequest:
        provisional = work_request_from_created(created_data)
        channel_id = provisional.channel_id
        if channel_id not in await self.registered_operation_channel_ids():
            raise ConfigurationError("The selected channel is not a registered operating channel")

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

    async def _system_channel_id(self) -> str:
        configured = self.settings.slack_system_channel_id.strip()
        if configured:
            return configured
        if self._resolved_system_channel_id:
            return self._resolved_system_channel_id
        channel_ids = await self._channel_ids()
        if len(channel_ids) != 1:
            raise ConfigurationError(
                "SLACK_SYSTEM_CHANNEL_ID is required when the app has joined multiple channels"
            )
        self._resolved_system_channel_id = channel_ids[0]
        return self._resolved_system_channel_id

    @staticmethod
    def _default_system_snapshot() -> dict[str, Any]:
        defaults = default_role_assignments()[WORKSPACE_ROLE_SCOPE]
        return {
            "schema_version": 2,
            "version": 0,
            "roles": {role_id: sorted(defaults.get(role_id, set())) for role_id in role_ids()},
            "approval_routes": {},
            "system_channels": {
                "audit_channel_id": None,
                "alerts_channel_id": None,
                "additional_operating_channel_ids": [],
            },
        }

    @staticmethod
    def _normalize_system_snapshot(data: dict[str, Any]) -> dict[str, Any]:
        normalized = SlackLedgerRepository._default_system_snapshot()
        normalized["version"] = int(data.get("version", 0))
        stored_roles = data.get("roles", {})
        for role_id in role_ids():
            normalized["roles"][role_id] = sorted(set(stored_roles.get(role_id, [])))
        recovery_admins = default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]
        normalized["roles"][SYSTEM_ADMIN_ROLE] = sorted(
            set(normalized["roles"][SYSTEM_ADMIN_ROLE]) | recovery_admins
        )
        normalized["approval_routes"] = copy.deepcopy(data.get("approval_routes", {}))
        stored_channels = data.get("system_channels", {})
        normalized["system_channels"] = {
            "audit_channel_id": stored_channels.get("audit_channel_id"),
            "alerts_channel_id": stored_channels.get("alerts_channel_id"),
            "additional_operating_channel_ids": sorted(
                set(stored_channels.get("additional_operating_channel_ids", []))
            ),
        }
        return normalized

    async def _configuration_data(
        self,
        root: dict,
        record_type: str,
        *,
        chunk_event_type: str = CONFIG_CHUNK,
    ) -> dict[str, Any]:
        _, root_payload = self._metadata(root)
        inline_record = root_payload.get("inline_record")
        if isinstance(inline_record, dict):
            inline_records = decode_chunks(
                [
                    {
                        "ts": root.get("ts", ""),
                        "metadata": {
                            "event_type": chunk_event_type,
                            "event_payload": inline_record,
                        },
                    }
                ],
                event_type=chunk_event_type,
            )
            inline = next(
                (item for item in inline_records if item["record_type"] == record_type), None
            )
            if inline is not None:
                return inline["data"]

        records = decode_chunks(
            await self._thread(root[CHANNEL_ID], root["ts"]), event_type=chunk_event_type
        )
        record = next((item for item in records if item["record_type"] == record_type), None)
        if record is None:
            raise ConfigurationError("Configuration record is incomplete")
        return record["data"]

    async def _legacy_system_snapshot(self) -> dict[str, Any]:
        snapshot = self._default_system_snapshot()
        roots = await self._roots(CONFIG_ROOT)
        roles_loaded = False
        loaded_routes: set[str] = set()
        for root in roots:
            _, payload = self._metadata(root)
            configuration_type = payload.get("configuration_type")
            key = str(payload.get("key") or "")
            if configuration_type == "access_roles" and not roles_loaded:
                try:
                    data = await self._configuration_data(root, ROLE_ASSIGNMENTS_SAVED)
                except ConfigurationError:
                    continue
                merged = empty_role_set()
                for scoped in data.get("scopes", {}).values():
                    for role_id in role_ids():
                        merged[role_id].update(scoped.get(role_id, []))
                snapshot["roles"] = {role_id: sorted(users) for role_id, users in merged.items()}
                roles_loaded = True
            if configuration_type in {"approval_route", "approval_rule"} and key:
                if key in loaded_routes:
                    continue
                try:
                    data = await self._configuration_data(root, RULE_SAVED)
                except ConfigurationError:
                    continue
                snapshot["approval_routes"][key] = {
                    "department_id": data.get("department_id"),
                    "budget_program_id": data.get("budget_program_id"),
                    "category_id": data.get("category_id"),
                    "approval_channel_id": data.get("approval_channel_id"),
                    "version": int(data.get("version", 0)),
                }
                loaded_routes.add(key)
        return self._normalize_system_snapshot(snapshot)

    async def _read_system_snapshot(self) -> dict[str, Any]:
        system_channel_id = await self._system_channel_id()
        cache_key = (self._shared_cache_key, system_channel_id)
        shared = _SHARED_CONFIG_CACHE.get(cache_key)
        if shared and self._cache_is_fresh(shared[0]):
            return copy.deepcopy(shared[1])

        response = await self.client.conversations_history(
            channel=system_channel_id,
            limit=20,
            include_all_metadata=True,
        )
        root = next(
            (
                {**message, CHANNEL_ID: system_channel_id}
                for message in response.get("messages", [])
                if self._metadata(message)[0] == SYSTEM_CONFIG_ROOT
            ),
            None,
        )
        if root is None:
            snapshot = await self._legacy_system_snapshot()
            return await self._write_system_snapshot(
                snapshot,
                actor="legacy-migration",
                audit_text=None,
            )

        data = await self._configuration_data(
            root,
            SYSTEM_CONFIG_SNAPSHOT,
            chunk_event_type=SYSTEM_CONFIG_CHUNK,
        )
        snapshot = self._normalize_system_snapshot(data)
        _SHARED_CONFIG_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(snapshot))
        return snapshot

    async def _write_system_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        actor: str,
        audit_text: str | None,
    ) -> dict[str, Any]:
        system_channel_id = await self._system_channel_id()
        normalized = self._normalize_system_snapshot(snapshot)
        normalized["version"] = int(normalized.get("version", 0)) + 1
        encoded = encode_chunks(record_type=SYSTEM_CONFIG_SNAPSHOT, data=normalized)
        inline_record = encoded[0] if len(encoded) == 1 else None
        response = await self.client.chat_postMessage(
            channel=system_channel_id,
            text=f"ERP system configuration snapshot v{normalized['version']}",
            metadata={
                "event_type": SYSTEM_CONFIG_ROOT,
                "event_payload": {
                    "schema_version": 2,
                    "snapshot_version": normalized["version"],
                    **({"inline_record": inline_record} if inline_record else {}),
                },
            },
            unfurl_links=False,
            unfurl_media=False,
        )
        self._invalidate_history(system_channel_id)
        if inline_record is None:
            await self._append_chunks(
                channel_id=system_channel_id,
                root_ts=response["ts"],
                metadata_event_type=SYSTEM_CONFIG_CHUNK,
                record_type=SYSTEM_CONFIG_SNAPSHOT,
                data=normalized,
                audit_text="System configuration snapshot payload",
            )
        cache_key = (self._shared_cache_key, system_channel_id)
        _SHARED_CONFIG_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(normalized))
        if audit_text:
            await self._write_audit(normalized, actor, audit_text)
        return normalized

    async def _write_audit(self, snapshot: dict[str, Any], actor: str, audit_text: str) -> None:
        channel_id = snapshot["system_channels"].get("audit_channel_id")
        if not channel_id:
            return
        await self.client.chat_postMessage(
            channel=channel_id,
            text=f"{audit_text} by <@{actor}>",
            metadata={
                "event_type": "system_audit_event",
                "event_payload": {
                    "configuration_version": snapshot["version"],
                    "actor_slack_user_id": actor,
                },
            },
            unfurl_links=False,
            unfurl_media=False,
        )

    async def report_alert(self, text: str) -> None:
        deduplication_key = (self._shared_cache_key, text)
        last_sent = _SHARED_ALERT_CACHE.get(deduplication_key)
        if last_sent is not None and time.monotonic() - last_sent < 60:
            return
        snapshot = await self._read_system_snapshot()
        channel_id = snapshot["system_channels"].get("alerts_channel_id")
        if channel_id:
            await self.client.chat_postMessage(channel=channel_id, text=text)
            _SHARED_ALERT_CACHE[deduplication_key] = time.monotonic()

    async def system_channels(self) -> dict[str, Any]:
        snapshot = await self._read_system_snapshot()
        return copy.deepcopy(snapshot["system_channels"])

    async def replace_system_channels(
        self,
        actor: str,
        *,
        audit_channel_id: str,
        alerts_channel_id: str,
        additional_operating_channel_ids: list[str],
    ) -> dict[str, Any]:
        await self.assert_system_admin(actor)
        system_channel_id = await self._system_channel_id()
        selected = {
            audit_channel_id,
            alerts_channel_id,
            *additional_operating_channel_ids,
        }
        if not audit_channel_id or not alerts_channel_id:
            raise ConfigurationError("Audit and alerts channels are required")
        if system_channel_id in selected or audit_channel_id == alerts_channel_id:
            raise ConfigurationError("System channels must be distinct")
        availability = await asyncio.gather(
            *(self.channel_is_available(channel_id) for channel_id in selected)
        )
        if not all(availability):
            raise ConfigurationError("The app must be a member of every selected channel")
        snapshot = await self._read_system_snapshot()
        snapshot["system_channels"] = {
            "audit_channel_id": audit_channel_id,
            "alerts_channel_id": alerts_channel_id,
            "additional_operating_channel_ids": sorted(set(additional_operating_channel_ids)),
        }
        return await self._write_system_snapshot(
            snapshot,
            actor=actor,
            audit_text="System channel configuration updated",
        )

    async def registered_operation_channel_ids(self) -> list[str]:
        snapshot = await self._read_system_snapshot()
        channel_ids = {
            route.get("approval_channel_id")
            for route in snapshot["approval_routes"].values()
            if route.get("approval_channel_id")
        }
        channel_ids.update(snapshot["system_channels"]["additional_operating_channel_ids"])
        channel_ids.discard(await self._system_channel_id())
        channel_ids.discard(snapshot["system_channels"].get("audit_channel_id"))
        channel_ids.discard(snapshot["system_channels"].get("alerts_channel_id"))
        return sorted(channel_ids)

    async def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        category = category_by_id(category_id, department_id)
        workflow = workflow_for_budget_node(category_id, department_id)
        if category is None or workflow is None:
            raise EntityNotFoundError("Approval workflow mapping not found")

        key = f"{department_id}:{category_id}"
        snapshot = await self._read_system_snapshot()
        route = snapshot["approval_routes"].get(key, {})
        approval_channel_id = route.get("approval_channel_id")
        version = int(route.get("version", 0))
        channel_members = (
            await self.channel_member_ids(approval_channel_id) if approval_channel_id else set()
        )

        steps = []
        for step in workflow.steps:
            approvers: set[str] = set()
            for role in step.approver_roles:
                approvers.update(await self.role_user_ids(role))
            approvers.intersection_update(channel_members)
            steps.append(
                ApprovalRuleStep(
                    name_en=step.name_en,
                    name_ko=step.name_ko,
                    approver_slack_user_ids=tuple(sorted(approvers)),
                    approver_roles=step.approver_roles,
                )
            )
        return ApprovalRule(
            department_id=department_id,
            budget_program_id=category.budget_program_id,
            category_id=category_id,
            approval_channel_id=approval_channel_id,
            steps=tuple(steps),
            workflow_id=workflow.id,
            workflow_name_en=workflow.name_en,
            workflow_name_ko=workflow.name_ko,
            version=version,
        )

    async def save_approval_route(
        self, actor: str, department_id: str, category_id: str, approval_channel_id: str
    ) -> ApprovalRule:
        await self.assert_system_admin(actor)
        if not approval_channel_id:
            raise ConfigurationError("Approval channel is required")
        if not await self.channel_is_available(approval_channel_id):
            raise ConfigurationError("The app is not a member of the approval channel")

        current = await self.get_rule(department_id, category_id)
        snapshot = await self._read_system_snapshot()
        key = f"{department_id}:{category_id}"
        snapshot["approval_routes"][key] = {
            "department_id": department_id,
            "budget_program_id": current.budget_program_id,
            "category_id": category_id,
            "approval_channel_id": approval_channel_id,
            "version": current.version + 1,
        }
        await self._write_system_snapshot(
            snapshot,
            actor=actor,
            audit_text=f"Approval route updated: {key}",
        )
        return await self.get_rule(department_id, category_id)

    async def role_assignments(self) -> dict[str, dict[str, set[str]]]:
        assignments = default_role_assignments()
        snapshot = await self._read_system_snapshot()
        workspace = assignments[WORKSPACE_ROLE_SCOPE]
        for role_id in role_ids():
            workspace[role_id].update(snapshot["roles"].get(role_id, []))
        return assignments

    async def role_user_ids(self, role_id: str) -> set[str]:
        assignments = await self.role_assignments()
        return set(assignments[WORKSPACE_ROLE_SCOPE].get(role_id, set()))

    async def system_admin_ids(self) -> set[str]:
        return await self.users_with_capability(MANAGE_CONFIGURATION)

    async def users_with_capability(
        self, capability: str, channel_id: str | None = None
    ) -> set[str]:
        assignments = await self.role_assignments()
        allowed: set[str] = set()
        scoped = assignments[WORKSPACE_ROLE_SCOPE]
        for role_id in roles_with_capability(capability):
            allowed.update(scoped.get(role_id, set()))
        if channel_id:
            allowed.intersection_update(await self.channel_member_ids(channel_id))
        return allowed

    async def assert_system_admin(self, actor: str) -> None:
        if actor in default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]:
            return
        if actor not in await self.users_with_capability(MANAGE_CONFIGURATION):
            raise ApprovalPermissionError("System administrator role required")

    async def assert_can_submit_request(self, actor: str, channel_id: str) -> None:
        await self.assert_channel_member(actor, channel_id)

    async def settlement_assigner_ids(self, channel_id: str | None = None) -> set[str]:
        return await self.users_with_capability(ASSIGN_SETTLEMENT, channel_id)

    async def assert_can_assign_settlement(self, actor: str, channel_id: str | None = None) -> None:
        if actor not in await self.settlement_assigner_ids(channel_id):
            raise ApprovalPermissionError("Approval or administrator role required")

    async def replace_role_assignments(
        self,
        actor: str,
        assignments: dict[str, dict[str, set[str]]],
    ) -> None:
        await self.assert_system_admin(actor)
        configured_roots = default_role_assignments()[WORKSPACE_ROLE_SCOPE][SYSTEM_ADMIN_ROLE]
        workspace = {
            role_id: set(assignments.get(WORKSPACE_ROLE_SCOPE, {}).get(role_id, set()))
            for role_id in role_ids()
        }
        workspace[SYSTEM_ADMIN_ROLE].update(configured_roots)
        if not workspace[SYSTEM_ADMIN_ROLE]:
            raise ConfigurationError("At least one administrator is required")
        snapshot = await self._read_system_snapshot()
        snapshot["roles"] = {role_id: sorted(users) for role_id, users in workspace.items()}
        await self._write_system_snapshot(
            snapshot,
            actor=actor,
            audit_text="Access role configuration updated",
        )
