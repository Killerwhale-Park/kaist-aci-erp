from __future__ import annotations

import asyncio
import copy
import time
from datetime import UTC, datetime
from typing import Any

from slack_sdk.errors import SlackApiError
from sqlalchemy import delete, or_, select

from app.application.work_lifecycle import lifecycle_adapter_for
from app.config.roles import (
    ASSIGN_SETTLEMENT,
    MANAGE_CONFIGURATION,
    SUBMIT_REQUEST,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    default_role_assignments,
    role_ids,
    roles_with_capability,
)
from app.database import Database
from app.domain.catalog import category_by_id, workflow_by_id, workflow_for_budget_node
from app.domain.enums import RequestStatus, WorkRequestStatus
from app.domain.models import (
    ApprovalRule,
    ApprovalRuleStep,
    ExpenseRequest,
    ResolvedApprovalWorkflow,
    WorkRequest,
)
from app.domain.work_requests import (
    WORK_REQUEST_COMPLETED,
    WORK_REQUEST_CREATED,
    apply_work_event,
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
)
from app.ledger.tables import (
    ApprovalRouteRecord,
    AuditEventRecord,
    ExpenseEventRecord,
    ExpenseRequestRecord,
    OperatingChannelRecord,
    RoleAssignmentRecord,
    SystemSettingsRecord,
    WorkRequestEventRecord,
    WorkRequestRecord,
)

CACHE_TTL_SECONDS = 30.0
_SHARED_MEMBER_CACHE: dict[tuple[int, str], tuple[float, set[str]]] = {}
_SHARED_CHANNEL_INFO_CACHE: dict[tuple[int, str], tuple[float, bool]] = {}
_SHARED_ALERT_CACHE: dict[tuple[int, str], float] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _record_id(locator: str) -> str:
    """Accept new UUID-only values and old channel|timestamp|UUID button values."""
    return locator.rsplit("|", 1)[-1]


class LedgerRepository:
    """Database-backed ledger with Slack used only for membership and presentation."""

    def __init__(self, client, database: Database) -> None:
        self.client = client
        self.database = database
        token = getattr(client, "token", None)
        self._shared_cache_key = hash(token) if token else id(client)

    @staticmethod
    def _cache_is_fresh(stored_at: float) -> bool:
        return time.monotonic() - stored_at < CACHE_TTL_SECONDS

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

    @staticmethod
    def _expense_projection(record: ExpenseRequestRecord, request: ExpenseRequest) -> None:
        summary = request_summary(request)
        record.status = request.status.value
        record.current_approver_slack_user_ids = summary["current_approver_slack_user_ids"]
        record.revision = request.revision
        record.updated_at = _utc_now()

    async def _expense_from_record(self, session, record: ExpenseRequestRecord) -> ExpenseRequest:
        result = await session.scalars(
            select(ExpenseEventRecord)
            .where(ExpenseEventRecord.request_id == record.id)
            .order_by(ExpenseEventRecord.sequence)
        )
        events = [
            {
                "ts": f"{event.sequence:020d}",
                "kind": event.kind,
                "actor": event.actor_slack_user_id,
                "data": copy.deepcopy(event.payload),
                "at": event.occurred_at.isoformat(),
            }
            for event in result
        ]
        return replay_events(events, message_ts=record.slack_message_ts)

    async def _expenses_from_records(
        self, session, records: list[ExpenseRequestRecord]
    ) -> list[ExpenseRequest]:
        if not records:
            return []
        request_ids = [record.id for record in records]
        result = await session.scalars(
            select(ExpenseEventRecord)
            .where(ExpenseEventRecord.request_id.in_(request_ids))
            .order_by(ExpenseEventRecord.request_id, ExpenseEventRecord.sequence)
        )
        grouped: dict[str, list[dict[str, Any]]] = {request_id: [] for request_id in request_ids}
        for event in result:
            grouped[event.request_id].append(
                {
                    "ts": f"{event.sequence:020d}",
                    "kind": event.kind,
                    "actor": event.actor_slack_user_id,
                    "data": copy.deepcopy(event.payload),
                    "at": event.occurred_at.isoformat(),
                }
            )
        return [
            replay_events(grouped[record.id], message_ts=record.slack_message_ts)
            for record in records
        ]

    async def create_request(self, created_data: dict[str, Any]) -> ExpenseRequest:
        provisional = request_from_created(created_data)
        if not await self.channel_is_available(provisional.approval_channel_id):
            raise ConfigurationError("The app is not a member of the configured channel")
        occurred_at = datetime.fromisoformat(created_data["submitted_at"])
        record = ExpenseRequestRecord(
            id=provisional.id,
            reference_number=provisional.reference_number,
            applicant_slack_user_id=provisional.applicant_slack_user_id,
            approval_channel_id=provisional.approval_channel_id,
            case_id=provisional.case_id,
            source_work_request_id=provisional.source_work_request_id,
            status=provisional.status.value,
            current_approver_slack_user_ids=request_summary(provisional)[
                "current_approver_slack_user_ids"
            ],
            revision=provisional.revision,
            event_version=1,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        event = ExpenseEventRecord(
            request_id=provisional.id,
            sequence=1,
            kind=REQUEST_CREATED,
            actor_slack_user_id=provisional.applicant_slack_user_id,
            payload=copy.deepcopy(created_data),
            occurred_at=occurred_at,
        )
        async with self.database.session() as session, session.begin():
            session.add(record)
            await session.flush()
            session.add(event)
        return provisional

    async def get_request(self, request_id: str) -> ExpenseRequest:
        actual_id = _record_id(request_id)
        async with self.database.session() as session:
            record = await session.get(ExpenseRequestRecord, actual_id)
            if record is None:
                raise EntityNotFoundError(f"Expense request not found: {actual_id}")
            return await self._expense_from_record(session, record)

    async def append_event(
        self, request_id: str, kind: str, actor: str, data: dict[str, Any] | None = None
    ) -> ExpenseRequest:
        actual_id = _record_id(request_id)
        payload = copy.deepcopy(data or {})
        async with self.database.session() as session, session.begin():
            record = await session.scalar(
                select(ExpenseRequestRecord)
                .where(ExpenseRequestRecord.id == actual_id)
                .with_for_update()
            )
            if record is None:
                raise EntityNotFoundError(f"Expense request not found: {actual_id}")
            current = await self._expense_from_record(session, record)
            validate_transition(current, kind, actor, payload)
            occurred_at = _utc_now()
            next_sequence = record.event_version + 1
            session.add(
                ExpenseEventRecord(
                    request_id=actual_id,
                    sequence=next_sequence,
                    kind=kind,
                    actor_slack_user_id=actor,
                    payload=payload,
                    occurred_at=occurred_at,
                )
            )
            record.event_version = next_sequence
            await session.flush()
            updated = await self._expense_from_record(session, record)
            self._expense_projection(record, updated)
        return updated

    async def list_for_applicant(self, slack_user_id: str) -> list[ExpenseRequest]:
        async with self.database.session() as session:
            records = list(
                await session.scalars(
                    select(ExpenseRequestRecord)
                    .where(ExpenseRequestRecord.applicant_slack_user_id == slack_user_id)
                    .order_by(ExpenseRequestRecord.created_at.desc())
                    .limit(100)
                )
            )
            return await self._expenses_from_records(session, records)

    async def list_active_for_applicant(self, slack_user_id: str) -> list[ExpenseRequest]:
        active = {
            RequestStatus.IN_APPROVAL.value,
            RequestStatus.CHANGES_REQUESTED.value,
            RequestStatus.APPROVED_PENDING_POST_EVIDENCE.value,
        }
        async with self.database.session() as session:
            records = list(
                await session.scalars(
                    select(ExpenseRequestRecord)
                    .where(
                        ExpenseRequestRecord.applicant_slack_user_id == slack_user_id,
                        ExpenseRequestRecord.status.in_(active),
                    )
                    .order_by(ExpenseRequestRecord.updated_at.desc())
                    .limit(50)
                )
            )
            return await self._expenses_from_records(session, records)

    async def list_pending_for_actor(self, slack_user_id: str) -> list[ExpenseRequest]:
        async with self.database.session() as session:
            # JSON containment differs between SQLite and PostgreSQL, so use an indexed status
            # predicate in SQL and apply the tiny approver-list predicate in Python.
            candidates = list(
                await session.scalars(
                    select(ExpenseRequestRecord)
                    .where(ExpenseRequestRecord.status == RequestStatus.IN_APPROVAL.value)
                    .order_by(ExpenseRequestRecord.created_at.desc())
                    .limit(500)
                )
            )
            records = [
                record
                for record in candidates
                if slack_user_id in record.current_approver_slack_user_ids
            ]
            return await self._expenses_from_records(session, records)

    async def update_request_view(
        self, request: ExpenseRequest, *, text: str, blocks: list[dict]
    ) -> None:
        message_ts = request.message_ts
        if message_ts:
            try:
                await self.client.chat_update(
                    channel=request.approval_channel_id,
                    ts=message_ts,
                    text=text,
                    blocks=blocks,
                )
                return
            except SlackApiError as error:
                if error.response.get("error") not in {"message_not_found", "channel_not_found"}:
                    raise
        response = await self.client.chat_postMessage(
            channel=request.approval_channel_id,
            text=text,
            blocks=blocks,
            unfurl_links=False,
            unfurl_media=False,
        )
        message_ts = response["ts"]
        async with self.database.session() as session, session.begin():
            record = await session.get(ExpenseRequestRecord, request.id)
            if record is None:
                raise EntityNotFoundError(f"Expense request not found: {request.id}")
            record.slack_message_ts = message_ts
            record.updated_at = _utc_now()
        request.message_ts = message_ts

    async def create_work_request(self, created_data: dict[str, Any]) -> WorkRequest:
        provisional = work_request_from_created(created_data)
        if provisional.channel_id not in await self.registered_operation_channel_ids():
            raise ConfigurationError("The selected channel is not a registered operating channel")
        occurred_at = datetime.fromisoformat(created_data["created_at"])
        summary = work_request_summary(provisional)
        record = WorkRequestRecord(
            id=provisional.id,
            reference_number=provisional.reference_number,
            kind=provisional.kind.value,
            requester_slack_user_id=provisional.requester_slack_user_id,
            originator_slack_user_id=provisional.originator_slack_user_id,
            assignee_slack_user_id=provisional.assignee_slack_user_id,
            case_id=provisional.case_id,
            parent_request_id=provisional.parent_request_id,
            channel_id=provisional.channel_id,
            status=provisional.status.value,
            current_step_order=provisional.current_step_order,
            current_approver_slack_user_ids=summary["current_approver_slack_user_ids"],
            event_version=1,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        event = WorkRequestEventRecord(
            request_id=provisional.id,
            sequence=1,
            kind=WORK_REQUEST_CREATED,
            actor_slack_user_id=provisional.requester_slack_user_id,
            payload=copy.deepcopy(created_data),
            occurred_at=occurred_at,
        )
        async with self.database.session() as session, session.begin():
            session.add(record)
            await session.flush()
            session.add(event)
        return provisional

    @staticmethod
    def _work_projection(record: WorkRequestRecord, request: WorkRequest) -> None:
        summary = work_request_summary(request)
        record.status = request.status.value
        record.current_step_order = request.current_step_order
        record.current_approver_slack_user_ids = summary["current_approver_slack_user_ids"]
        record.updated_at = _utc_now()

    async def _work_request_from_record(self, session, record: WorkRequestRecord) -> WorkRequest:
        result = await session.scalars(
            select(WorkRequestEventRecord)
            .where(WorkRequestEventRecord.request_id == record.id)
            .order_by(WorkRequestEventRecord.sequence)
        )
        events = [
            {
                "ts": f"{event.sequence:020d}",
                "kind": event.kind,
                "actor": event.actor_slack_user_id,
                "data": copy.deepcopy(event.payload),
                "at": event.occurred_at.isoformat(),
            }
            for event in result
        ]
        return replay_work_events(events, message_ts=record.slack_message_ts)

    async def _work_requests_from_records(
        self, session, records: list[WorkRequestRecord]
    ) -> list[WorkRequest]:
        if not records:
            return []
        request_ids = [record.id for record in records]
        result = await session.scalars(
            select(WorkRequestEventRecord)
            .where(WorkRequestEventRecord.request_id.in_(request_ids))
            .order_by(WorkRequestEventRecord.request_id, WorkRequestEventRecord.sequence)
        )
        grouped: dict[str, list[dict[str, Any]]] = {request_id: [] for request_id in request_ids}
        for event in result:
            grouped[event.request_id].append(
                {
                    "ts": f"{event.sequence:020d}",
                    "kind": event.kind,
                    "actor": event.actor_slack_user_id,
                    "data": copy.deepcopy(event.payload),
                    "at": event.occurred_at.isoformat(),
                }
            )
        return [
            replay_work_events(grouped[record.id], message_ts=record.slack_message_ts)
            for record in records
        ]

    async def get_work_request(self, request_id: str) -> WorkRequest:
        actual_id = _record_id(request_id)
        async with self.database.session() as session:
            record = await session.get(WorkRequestRecord, actual_id)
            if record is None:
                raise EntityNotFoundError(f"Work request not found: {actual_id}")
            return await self._work_request_from_record(session, record)

    async def append_work_event(
        self,
        request_id: str,
        kind: str,
        actor: str,
        data: dict[str, Any] | None = None,
    ) -> WorkRequest:
        actual_id = _record_id(request_id)
        payload = copy.deepcopy(data or {})
        async with self.database.session() as session, session.begin():
            record = await session.scalar(
                select(WorkRequestRecord).where(WorkRequestRecord.id == actual_id).with_for_update()
            )
            if record is None:
                raise EntityNotFoundError(f"Work request not found: {actual_id}")
            current = await self._work_request_from_record(session, record)
            candidate = copy.deepcopy(current)
            apply_work_event(candidate, kind, actor, _utc_now(), payload)
            occurred_at = _utc_now()
            next_sequence = record.event_version + 1
            session.add(
                WorkRequestEventRecord(
                    request_id=actual_id,
                    sequence=next_sequence,
                    kind=kind,
                    actor_slack_user_id=actor,
                    payload=payload,
                    occurred_at=occurred_at,
                )
            )
            record.event_version = next_sequence
            await session.flush()
            updated = await self._work_request_from_record(session, record)
            self._work_projection(record, updated)
        return updated

    async def complete_work_request(
        self,
        request_id: str,
        actor: str,
        *,
        successor_type: str | None = None,
        successor_id: str | None = None,
    ) -> WorkRequest:
        current = await self.get_work_request(request_id)
        lifecycle_adapter_for(current).assert_completion(
            current,
            actor,
            successor_type,
            successor_id,
        )
        return await self.append_work_event(
            request_id,
            WORK_REQUEST_COMPLETED,
            actor,
            {
                **({"successor_type": successor_type} if successor_type else {}),
                **({"successor_id": successor_id} if successor_id else {}),
            },
        )

    async def handoff_work_request(
        self,
        source_request_id: str,
        actor: str,
        created_data: dict[str, Any],
    ) -> tuple[WorkRequest, WorkRequest]:
        """Atomically complete one task and create its configured successor."""
        successor = work_request_from_created(created_data)
        actual_id = _record_id(source_request_id)
        if successor.parent_request_id != actual_id:
            raise ConfigurationError("Successor does not reference its source work request")
        if successor.channel_id not in await self.registered_operation_channel_ids():
            raise ConfigurationError("The selected channel is not a registered operating channel")
        async with self.database.session() as session, session.begin():
            source_record = await session.scalar(
                select(WorkRequestRecord).where(WorkRequestRecord.id == actual_id).with_for_update()
            )
            if source_record is None:
                raise EntityNotFoundError(f"Work request not found: {actual_id}")
            source = await self._work_request_from_record(session, source_record)
            lifecycle_adapter_for(source).assert_completion(
                source,
                actor,
                f"{successor.kind.value}_WORK_REQUEST",
                successor.id,
            )
            candidate = copy.deepcopy(source)
            occurred_at = _utc_now()
            apply_work_event(
                candidate,
                WORK_REQUEST_COMPLETED,
                actor,
                occurred_at,
                {
                    "successor_type": f"{successor.kind.value}_WORK_REQUEST",
                    "successor_id": successor.id,
                },
            )
            successor_summary = work_request_summary(successor)
            successor_record = WorkRequestRecord(
                id=successor.id,
                reference_number=successor.reference_number,
                kind=successor.kind.value,
                requester_slack_user_id=successor.requester_slack_user_id,
                originator_slack_user_id=successor.originator_slack_user_id,
                assignee_slack_user_id=successor.assignee_slack_user_id,
                case_id=successor.case_id,
                parent_request_id=successor.parent_request_id,
                channel_id=successor.channel_id,
                status=successor.status.value,
                current_step_order=successor.current_step_order,
                current_approver_slack_user_ids=successor_summary[
                    "current_approver_slack_user_ids"
                ],
                event_version=1,
                created_at=successor.created_at,
                updated_at=successor.created_at,
            )
            session.add(successor_record)
            await session.flush()
            session.add_all(
                (
                    WorkRequestEventRecord(
                        request_id=successor.id,
                        sequence=1,
                        kind=WORK_REQUEST_CREATED,
                        actor_slack_user_id=actor,
                        payload=copy.deepcopy(created_data),
                        occurred_at=successor.created_at,
                    ),
                    WorkRequestEventRecord(
                        request_id=actual_id,
                        sequence=source_record.event_version + 1,
                        kind=WORK_REQUEST_COMPLETED,
                        actor_slack_user_id=actor,
                        payload={
                            "successor_type": f"{successor.kind.value}_WORK_REQUEST",
                            "successor_id": successor.id,
                        },
                        occurred_at=occurred_at,
                    ),
                )
            )
            source_record.event_version += 1
            self._work_projection(source_record, candidate)
        return candidate, successor

    async def list_active_work_for_user(self, slack_user_id: str) -> list[WorkRequest]:
        active = {
            WorkRequestStatus.IN_APPROVAL.value,
            WorkRequestStatus.ACTION_REQUIRED.value,
            WorkRequestStatus.OPEN.value,
        }
        async with self.database.session() as session:
            records = list(
                await session.scalars(
                    select(WorkRequestRecord)
                    .where(
                        WorkRequestRecord.status.in_(active),
                        or_(
                            WorkRequestRecord.requester_slack_user_id == slack_user_id,
                            WorkRequestRecord.originator_slack_user_id == slack_user_id,
                        ),
                    )
                    .order_by(WorkRequestRecord.updated_at.desc())
                    .limit(50)
                )
            )
            return await self._work_requests_from_records(session, records)

    async def list_actionable_work_for_actor(self, slack_user_id: str) -> list[WorkRequest]:
        async with self.database.session() as session:
            candidates = list(
                await session.scalars(
                    select(WorkRequestRecord)
                    .where(
                        WorkRequestRecord.status.in_(
                            {
                                WorkRequestStatus.IN_APPROVAL.value,
                                WorkRequestStatus.ACTION_REQUIRED.value,
                                WorkRequestStatus.OPEN.value,
                            }
                        )
                    )
                    .order_by(WorkRequestRecord.updated_at.desc())
                    .limit(500)
                )
            )
            records = [
                record
                for record in candidates
                if (
                    record.status == WorkRequestStatus.IN_APPROVAL.value
                    and slack_user_id in record.current_approver_slack_user_ids
                )
                or (
                    record.status
                    in {
                        WorkRequestStatus.ACTION_REQUIRED.value,
                        WorkRequestStatus.OPEN.value,
                    }
                    and record.assignee_slack_user_id == slack_user_id
                )
            ]
            return await self._work_requests_from_records(session, records)

    async def update_work_request_view(
        self, request: WorkRequest, *, text: str, blocks: list[dict]
    ) -> None:
        message_ts = request.message_ts
        if message_ts:
            try:
                await self.client.chat_update(
                    channel=request.channel_id,
                    ts=message_ts,
                    text=text,
                    blocks=blocks,
                )
                return
            except SlackApiError as error:
                if error.response.get("error") not in {"message_not_found", "channel_not_found"}:
                    raise
        response = await self.client.chat_postMessage(
            channel=request.channel_id,
            text=text,
            blocks=blocks,
            unfurl_links=False,
            unfurl_media=False,
        )
        message_ts = response["ts"]
        async with self.database.session() as session, session.begin():
            record = await session.get(WorkRequestRecord, request.id)
            if record is None:
                raise EntityNotFoundError(f"Work request not found: {request.id}")
            record.slack_message_ts = message_ts
            record.updated_at = _utc_now()
        request.message_ts = message_ts

    async def _audit(
        self,
        session,
        *,
        event_type: str,
        actor: str,
        entity_type: str,
        entity_id: str | None,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditEventRecord(
                event_type=event_type,
                actor_slack_user_id=actor,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=copy.deepcopy(detail or {}),
                summary=summary,
                created_at=_utc_now(),
            )
        )

    async def _publish_audit(self, channel_id: str | None, actor: str, text: str) -> None:
        if not channel_id:
            return
        try:
            await self.client.chat_postMessage(channel=channel_id, text=f"{text} by <@{actor}>")
        except Exception:
            # The database audit row is authoritative; Slack is a best-effort projection.
            return

    async def system_channels(self) -> dict[str, Any]:
        async with self.database.session() as session:
            settings = await session.get(SystemSettingsRecord, 1)
            operating = list(await session.scalars(select(OperatingChannelRecord.channel_id)))
            return {
                "audit_channel_id": settings.audit_channel_id if settings else None,
                "alerts_channel_id": settings.alerts_channel_id if settings else None,
                "additional_operating_channel_ids": sorted(operating),
            }

    async def replace_system_channels(
        self,
        actor: str,
        *,
        audit_channel_id: str,
        alerts_channel_id: str,
        additional_operating_channel_ids: list[str],
    ) -> dict[str, Any]:
        await self.assert_system_admin(actor)
        selected = {audit_channel_id, alerts_channel_id, *additional_operating_channel_ids}
        if not audit_channel_id or not alerts_channel_id:
            raise ConfigurationError("Audit and alerts channels are required")
        if audit_channel_id == alerts_channel_id:
            raise ConfigurationError("Audit and alerts channels must be distinct")
        if audit_channel_id in additional_operating_channel_ids or (
            alerts_channel_id in additional_operating_channel_ids
        ):
            raise ConfigurationError("System channels cannot also be operating channels")
        availability = await asyncio.gather(
            *(self.channel_is_available(channel_id) for channel_id in selected)
        )
        if not all(availability):
            raise ConfigurationError("The app must be a member of every selected channel")
        async with self.database.session() as session, session.begin():
            settings = await session.scalar(
                select(SystemSettingsRecord).where(SystemSettingsRecord.id == 1).with_for_update()
            )
            if settings is None:
                settings = SystemSettingsRecord(id=1, version=0)
                session.add(settings)
            settings.audit_channel_id = audit_channel_id
            settings.alerts_channel_id = alerts_channel_id
            settings.version += 1
            settings.updated_by_slack_user_id = actor
            settings.updated_at = _utc_now()
            await session.execute(delete(OperatingChannelRecord))
            session.add_all(
                OperatingChannelRecord(
                    channel_id=channel_id,
                    context={"source": "manual"},
                    registered_by_slack_user_id=actor,
                )
                for channel_id in sorted(set(additional_operating_channel_ids))
            )
            await self._audit(
                session,
                event_type="SYSTEM_CHANNELS_UPDATED",
                actor=actor,
                entity_type="system_settings",
                entity_id="1",
                summary="System channel configuration updated",
                detail={
                    "audit_channel_id": audit_channel_id,
                    "alerts_channel_id": alerts_channel_id,
                    "additional_operating_channel_ids": sorted(
                        set(additional_operating_channel_ids)
                    ),
                },
            )
        await self._publish_audit(audit_channel_id, actor, "System channel configuration updated")
        return await self.system_channels()

    async def registered_operation_channel_ids(self) -> list[str]:
        async with self.database.session() as session:
            manual = set(await session.scalars(select(OperatingChannelRecord.channel_id)))
            routes = set(await session.scalars(select(ApprovalRouteRecord.approval_channel_id)))
        return sorted(manual | routes)

    async def submission_configuration(self) -> tuple[bool, bool]:
        """Return whether purchase channels and expense routes are configured."""
        async with self.database.session() as session:
            manual_channel = await session.scalar(
                select(OperatingChannelRecord.channel_id).limit(1)
            )
            route_channel = await session.scalar(
                select(ApprovalRouteRecord.approval_channel_id).limit(1)
            )
        return bool(manual_channel or route_channel), bool(route_channel)

    async def assert_operating_channel(self, channel_id: str) -> None:
        if channel_id not in await self.registered_operation_channel_ids():
            raise ConfigurationError("The selected channel is not a registered operating channel")
        if not await self.channel_is_available(channel_id):
            raise ConfigurationError("The app is not a member of the selected operating channel")

    async def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        category = category_by_id(category_id, department_id)
        workflow = workflow_for_budget_node(category_id, department_id)
        if category is None or workflow is None:
            raise EntityNotFoundError("Approval workflow mapping not found")
        async with self.database.session() as session:
            route = await session.get(ApprovalRouteRecord, (department_id, category_id))
        approval_channel_id = route.approval_channel_id if route else None
        version = route.version if route else 0
        resolved = (
            await self.resolve_approval_workflow(workflow.id, approval_channel_id)
            if approval_channel_id
            else ResolvedApprovalWorkflow(
                id=workflow.id,
                name_en=workflow.name_en,
                name_ko=workflow.name_ko,
                steps=tuple(
                    ApprovalRuleStep(
                        name_en=step.name_en,
                        name_ko=step.name_ko,
                        approver_slack_user_ids=(),
                        approver_roles=step.approver_roles,
                    )
                    for step in workflow.steps
                ),
            )
        )
        return ApprovalRule(
            department_id=department_id,
            budget_program_id=category.budget_program_id,
            category_id=category_id,
            approval_channel_id=approval_channel_id,
            steps=resolved.steps,
            workflow_id=workflow.id,
            workflow_name_en=workflow.name_en,
            workflow_name_ko=workflow.name_ko,
            version=version,
        )

    async def resolve_approval_workflow(
        self,
        workflow_id: str,
        channel_id: str,
        *,
        actor_bindings: dict[str, set[str]] | None = None,
    ) -> ResolvedApprovalWorkflow:
        """Resolve a code-defined workflow without knowing which entity will use it."""
        workflow = workflow_by_id(workflow_id)
        if workflow is None:
            raise EntityNotFoundError(f"Approval workflow not found: {workflow_id}")
        channel_members = await self.channel_member_ids(channel_id)
        workspace = (await self.role_assignments())[WORKSPACE_ROLE_SCOPE]
        bindings = actor_bindings or {}
        resolved_steps: list[ApprovalRuleStep] = []
        for step in workflow.steps:
            role_members: set[str] = set()
            for role in step.approver_roles:
                role_members.update(workspace.get(role, set()))
            if step.actor_binding:
                approvers = set(bindings.get(step.actor_binding, set()))
                if step.approver_roles:
                    approvers.intersection_update(role_members)
            else:
                approvers = role_members
            approvers.intersection_update(channel_members)
            resolved_steps.append(
                ApprovalRuleStep(
                    name_en=step.name_en,
                    name_ko=step.name_ko,
                    approver_slack_user_ids=tuple(sorted(approvers)),
                    approver_roles=step.approver_roles,
                )
            )
        return ResolvedApprovalWorkflow(
            id=workflow.id,
            name_en=workflow.name_en,
            name_ko=workflow.name_ko,
            steps=tuple(resolved_steps),
        )

    async def save_approval_route(
        self, actor: str, department_id: str, category_id: str, approval_channel_id: str
    ) -> ApprovalRule:
        await self.assert_system_admin(actor)
        if not approval_channel_id or not await self.channel_is_available(approval_channel_id):
            raise ConfigurationError("The app is not a member of the approval channel")
        category = category_by_id(category_id, department_id)
        workflow = workflow_for_budget_node(category_id, department_id)
        if category is None or workflow is None:
            raise EntityNotFoundError("Approval workflow mapping not found")
        key = f"{department_id}:{category_id}"
        audit_channel_id: str | None = None
        async with self.database.session() as session, session.begin():
            route = await session.scalar(
                select(ApprovalRouteRecord)
                .where(
                    ApprovalRouteRecord.department_id == department_id,
                    ApprovalRouteRecord.category_id == category_id,
                )
                .with_for_update()
            )
            if route is None:
                route = ApprovalRouteRecord(
                    department_id=department_id,
                    category_id=category_id,
                    budget_program_id=category.budget_program_id,
                    approval_channel_id=approval_channel_id,
                    version=1,
                    updated_by_slack_user_id=actor,
                )
                session.add(route)
            else:
                route.approval_channel_id = approval_channel_id
                route.version += 1
                route.updated_by_slack_user_id = actor
                route.updated_at = _utc_now()
            settings = await session.get(SystemSettingsRecord, 1)
            audit_channel_id = settings.audit_channel_id if settings else None
            await self._audit(
                session,
                event_type="APPROVAL_ROUTE_UPDATED",
                actor=actor,
                entity_type="approval_route",
                entity_id=key,
                summary=f"Approval route updated: {key}",
                detail={"approval_channel_id": approval_channel_id},
            )
        await self._publish_audit(audit_channel_id, actor, f"Approval route updated: {key}")
        return await self.get_rule(department_id, category_id)

    async def role_assignments(self) -> dict[str, dict[str, set[str]]]:
        assignments = default_role_assignments()
        async with self.database.session() as session:
            rows = list(await session.scalars(select(RoleAssignmentRecord)))
        workspace = assignments[WORKSPACE_ROLE_SCOPE]
        for row in rows:
            if row.scope == WORKSPACE_ROLE_SCOPE and row.role_id in workspace:
                workspace[row.role_id].add(row.slack_user_id)
        return assignments

    async def role_user_ids(self, role_id: str) -> set[str]:
        return set((await self.role_assignments())[WORKSPACE_ROLE_SCOPE].get(role_id, set()))

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
        if actor not in await self.users_with_capability(SUBMIT_REQUEST, channel_id):
            raise ApprovalPermissionError("Eligible requester role and channel membership required")

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
        audit_channel_id: str | None = None
        async with self.database.session() as session, session.begin():
            await session.execute(delete(RoleAssignmentRecord))
            session.add_all(
                RoleAssignmentRecord(
                    scope=WORKSPACE_ROLE_SCOPE,
                    role_id=role_id,
                    slack_user_id=user_id,
                    assigned_by_slack_user_id=actor,
                )
                for role_id, users in workspace.items()
                for user_id in sorted(users)
            )
            settings = await session.get(SystemSettingsRecord, 1)
            audit_channel_id = settings.audit_channel_id if settings else None
            await self._audit(
                session,
                event_type="ROLE_ASSIGNMENTS_UPDATED",
                actor=actor,
                entity_type="role_assignments",
                entity_id=WORKSPACE_ROLE_SCOPE,
                summary="Access role configuration updated",
                detail={role_id: sorted(users) for role_id, users in workspace.items()},
            )
        await self._publish_audit(audit_channel_id, actor, "Access role configuration updated")

    async def report_alert(self, text: str) -> None:
        deduplication_key = (self._shared_cache_key, text)
        last_sent = _SHARED_ALERT_CACHE.get(deduplication_key)
        if last_sent is not None and time.monotonic() - last_sent < 60:
            return
        async with self.database.session() as session, session.begin():
            settings = await session.get(SystemSettingsRecord, 1)
            channel_id = settings.alerts_channel_id if settings else None
            await self._audit(
                session,
                event_type="OPERATIONAL_ALERT",
                actor="system",
                entity_type="application",
                entity_id=None,
                summary=text,
            )
        if channel_id:
            await self.client.chat_postMessage(channel=channel_id, text=text)
        _SHARED_ALERT_CACHE[deduplication_key] = time.monotonic()
