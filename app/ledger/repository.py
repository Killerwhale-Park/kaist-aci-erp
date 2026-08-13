from __future__ import annotations

import asyncio
import copy
import time
from datetime import UTC, datetime
from typing import Any

from slack_sdk.errors import SlackApiError
from sqlalchemy import delete, select

from app.config.roles import (
    ASSIGN_SETTLEMENT,
    MANAGE_CONFIGURATION,
    SYSTEM_ADMIN_ROLE,
    WORKSPACE_ROLE_SCOPE,
    default_role_assignments,
    role_ids,
    roles_with_capability,
)
from app.database import Database
from app.domain.catalog import category_by_id, workflow_for_budget_node
from app.domain.enums import RequestStatus, WorkRequestStatus
from app.domain.models import ApprovalRule, ApprovalRuleStep, ExpenseRequest, WorkRequest
from app.domain.work_requests import (
    WORK_REQUEST_COMPLETED,
    WORK_REQUEST_CREATED,
    replay_work_events,
    work_request_from_created,
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
            session.add_all((record, event))
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
        record = WorkRequestRecord(
            id=provisional.id,
            reference_number=provisional.reference_number,
            kind=provisional.kind.value,
            requester_slack_user_id=provisional.requester_slack_user_id,
            assignee_slack_user_id=provisional.assignee_slack_user_id,
            channel_id=provisional.channel_id,
            status=provisional.status.value,
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
            session.add_all((record, event))
        return provisional

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

    async def get_work_request(self, request_id: str) -> WorkRequest:
        actual_id = _record_id(request_id)
        async with self.database.session() as session:
            record = await session.get(WorkRequestRecord, actual_id)
            if record is None:
                raise EntityNotFoundError(f"Work request not found: {actual_id}")
            return await self._work_request_from_record(session, record)

    async def complete_work_request(self, request_id: str, actor: str) -> WorkRequest:
        actual_id = _record_id(request_id)
        async with self.database.session() as session, session.begin():
            record = await session.scalar(
                select(WorkRequestRecord).where(WorkRequestRecord.id == actual_id).with_for_update()
            )
            if record is None:
                raise EntityNotFoundError(f"Work request not found: {actual_id}")
            current = await self._work_request_from_record(session, record)
            allowed = {
                current.requester_slack_user_id,
                current.assignee_slack_user_id,
                *(await self.system_admin_ids()),
            }
            if actor not in allowed:
                raise ApprovalPermissionError("Only a participant can complete this work request")
            if current.status != WorkRequestStatus.OPEN:
                raise InvalidStateTransitionError("Work request is already completed")
            occurred_at = _utc_now()
            next_sequence = record.event_version + 1
            session.add(
                WorkRequestEventRecord(
                    request_id=actual_id,
                    sequence=next_sequence,
                    kind=WORK_REQUEST_COMPLETED,
                    actor_slack_user_id=actor,
                    payload={},
                    occurred_at=occurred_at,
                )
            )
            record.event_version = next_sequence
            record.status = WorkRequestStatus.COMPLETED.value
            record.updated_at = occurred_at
            await session.flush()
            updated = await self._work_request_from_record(session, record)
        return updated

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

    async def get_rule(self, department_id: str, category_id: str) -> ApprovalRule:
        category = category_by_id(category_id, department_id)
        workflow = workflow_for_budget_node(category_id, department_id)
        if category is None or workflow is None:
            raise EntityNotFoundError("Approval workflow mapping not found")
        async with self.database.session() as session:
            route = await session.get(ApprovalRouteRecord, (department_id, category_id))
        approval_channel_id = route.approval_channel_id if route else None
        version = route.version if route else 0
        channel_members = (
            await self.channel_member_ids(approval_channel_id) if approval_channel_id else set()
        )
        assignments = await self.role_assignments()
        workspace = assignments[WORKSPACE_ROLE_SCOPE]
        steps = []
        for step in workflow.steps:
            approvers: set[str] = set()
            for role in step.approver_roles:
                approvers.update(workspace.get(role, set()))
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
