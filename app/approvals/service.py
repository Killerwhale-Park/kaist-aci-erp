import uuid

from sqlalchemy.orm import Session

from app.approvals.authorization import ApprovalAuthorizer
from app.approvals.engine import ApprovalEngine
from app.db.enums import AuditEventType, RequestStatus
from app.db.models import ApprovalActionLog, ApprovalStep, ExpenseRequest, UserProfile
from app.db.repository import ExpenseRequestRepository
from app.exceptions import ApprovalPermissionError, DomainValidationError
from app.i18n import t


class ApprovalService:
    def __init__(
        self,
        session: Session,
        engine: ApprovalEngine | None = None,
        authorizer: ApprovalAuthorizer | None = None,
    ) -> None:
        self.session = session
        self.engine = engine or ApprovalEngine()
        self.authorizer = authorizer or ApprovalAuthorizer()
        self.repository = ExpenseRequestRepository(session)

    def approve(self, request_id: uuid.UUID | str, actor_slack_user_id: str) -> ExpenseRequest:
        request, step = self._authorized_request(request_id, actor_slack_user_id)
        self.engine.approve(request, step, actor_slack_user_id)
        self._audit(
            request,
            AuditEventType.APPROVAL_STEP_APPROVED,
            actor_slack_user_id,
            step,
            {"step_order": step.step_order},
        )
        if request.status == RequestStatus.COMPLETED:
            self._audit(request, AuditEventType.REQUEST_COMPLETED, actor_slack_user_id)
        self.session.flush()
        return request

    def request_changes(
        self, request_id: uuid.UUID | str, actor_slack_user_id: str, reason: str
    ) -> ExpenseRequest:
        self._validate_reason(reason)
        request, step = self._authorized_request(request_id, actor_slack_user_id)
        self.engine.request_changes(request, step, actor_slack_user_id, reason.strip())
        self._audit(
            request,
            AuditEventType.CHANGES_REQUESTED,
            actor_slack_user_id,
            step,
            {"reason": reason.strip(), "step_order": step.step_order},
        )
        self.session.flush()
        return request

    def reject(
        self, request_id: uuid.UUID | str, actor_slack_user_id: str, reason: str
    ) -> ExpenseRequest:
        self._validate_reason(reason)
        request, step = self._authorized_request(request_id, actor_slack_user_id)
        self.engine.reject(request, step, actor_slack_user_id, reason.strip())
        self._audit(
            request,
            AuditEventType.REQUEST_REJECTED,
            actor_slack_user_id,
            step,
            {"reason": reason.strip(), "step_order": step.step_order},
        )
        self.session.flush()
        return request

    def can_actor_approve(self, request: ExpenseRequest, actor_slack_user_id: str) -> bool:
        if request.current_step_order is None:
            return False
        try:
            step = self.engine.current_step(request)
        except Exception:
            return False
        profile = self.session.get(UserProfile, actor_slack_user_id)
        return self.authorizer.can_act(actor_slack_user_id, request, step, profile)

    def assert_actor_can_approve(
        self, request_id: uuid.UUID | str, actor_slack_user_id: str
    ) -> ExpenseRequest:
        request, _ = self._authorized_request(request_id, actor_slack_user_id, for_update=False)
        return request

    def _authorized_request(
        self,
        request_id: uuid.UUID | str,
        actor_slack_user_id: str,
        *,
        for_update: bool = True,
    ) -> tuple[ExpenseRequest, ApprovalStep]:
        request = self.repository.get(request_id, for_update=for_update)
        step = self.engine.current_step(request)
        profile = self.session.get(UserProfile, actor_slack_user_id)
        if not self.authorizer.can_act(actor_slack_user_id, request, step, profile):
            raise ApprovalPermissionError("The Slack user cannot act on this approval step")
        return request, step

    def _audit(
        self,
        request: ExpenseRequest,
        event_type: AuditEventType,
        actor_slack_user_id: str,
        step: ApprovalStep | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.session.add(
            ApprovalActionLog(
                request_id=request.id,
                event_type=event_type,
                actor_slack_user_id=actor_slack_user_id,
                approval_step_id=step.id if step else None,
                event_metadata=metadata or {},
            )
        )

    def _validate_reason(self, reason: str) -> None:
        if not reason.strip():
            raise DomainValidationError(
                "A reason is required",
                {"decision_reason": t("reason_required")},
            )
