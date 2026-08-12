from app.db.enums import ApprovalStepStatus, RequestStatus
from app.db.models import ApprovalStep, ExpenseRequest, utc_now
from app.exceptions import ConfigurationError, InvalidStateTransitionError
from app.expenses.evidence import required_post_evidence_complete


class ApprovalEngine:
    def initialize(self, request: ExpenseRequest) -> None:
        steps = sorted(request.approval_steps, key=lambda step: step.step_order)
        if not steps:
            raise ConfigurationError("The selected approval workflow has no steps")
        for index, step in enumerate(steps):
            step.status = ApprovalStepStatus.PENDING if index == 0 else ApprovalStepStatus.WAITING
        request.status = RequestStatus.IN_APPROVAL
        request.current_step_order = steps[0].step_order
        request.submitted_at = utc_now()

    def approve(self, request: ExpenseRequest, step: ApprovalStep, actor_user_id: str) -> None:
        self._assert_current_step(request, step)
        step.status = ApprovalStepStatus.APPROVED
        step.acted_by_slack_user_id = actor_user_id
        step.acted_at = utc_now()
        step.comment = None

        next_step = next(
            (
                candidate
                for candidate in sorted(request.approval_steps, key=lambda item: item.step_order)
                if candidate.step_order > step.step_order
                and candidate.status == ApprovalStepStatus.WAITING
            ),
            None,
        )
        if next_step is not None:
            next_step.status = ApprovalStepStatus.PENDING
            request.current_step_order = next_step.step_order
            request.status = RequestStatus.IN_APPROVAL
            return

        request.current_step_order = None
        request.status = (
            RequestStatus.COMPLETED
            if required_post_evidence_complete(request.evidence_submissions)
            else RequestStatus.APPROVED_PENDING_POST_EVIDENCE
        )

    def request_changes(
        self, request: ExpenseRequest, step: ApprovalStep, actor_user_id: str, reason: str
    ) -> None:
        self._assert_current_step(request, step)
        step.status = ApprovalStepStatus.CHANGES_REQUESTED
        step.acted_by_slack_user_id = actor_user_id
        step.acted_at = utc_now()
        step.comment = reason
        request.status = RequestStatus.CHANGES_REQUESTED

    def reject(
        self, request: ExpenseRequest, step: ApprovalStep, actor_user_id: str, reason: str
    ) -> None:
        self._assert_current_step(request, step)
        step.status = ApprovalStepStatus.REJECTED
        step.acted_by_slack_user_id = actor_user_id
        step.acted_at = utc_now()
        step.comment = reason
        request.status = RequestStatus.REJECTED
        request.current_step_order = None

    def resubmit(self, request: ExpenseRequest) -> ApprovalStep:
        if request.status != RequestStatus.CHANGES_REQUESTED:
            raise InvalidStateTransitionError("Only a returned request can be resubmitted")
        step = next(
            (
                candidate
                for candidate in request.approval_steps
                if candidate.status == ApprovalStepStatus.CHANGES_REQUESTED
            ),
            None,
        )
        if step is None:
            raise InvalidStateTransitionError("The returned approval step was not found")
        step.status = ApprovalStepStatus.PENDING
        step.acted_by_slack_user_id = None
        step.acted_at = None
        step.comment = None
        request.status = RequestStatus.IN_APPROVAL
        request.current_step_order = step.step_order
        request.revision += 1
        return step

    def evaluate_post_evidence(self, request: ExpenseRequest) -> None:
        if request.status != RequestStatus.APPROVED_PENDING_POST_EVIDENCE:
            raise InvalidStateTransitionError("This request is not awaiting post-event evidence")
        if required_post_evidence_complete(request.evidence_submissions):
            request.status = RequestStatus.COMPLETED

    def current_step(self, request: ExpenseRequest) -> ApprovalStep:
        step = next(
            (
                candidate
                for candidate in request.approval_steps
                if candidate.step_order == request.current_step_order
            ),
            None,
        )
        if step is None:
            raise InvalidStateTransitionError("The current approval step was not found")
        return step

    def _assert_current_step(self, request: ExpenseRequest, step: ApprovalStep) -> None:
        if request.status != RequestStatus.IN_APPROVAL:
            raise InvalidStateTransitionError("The request is not in approval")
        if request.current_step_order != step.step_order:
            raise InvalidStateTransitionError("This is not the current approval step")
        if step.status != ApprovalStepStatus.PENDING:
            raise InvalidStateTransitionError("The current approval step is not pending")
