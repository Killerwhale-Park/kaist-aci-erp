from app.db.enums import ApprovalStepStatus, RequestStatus
from app.db.models import ApprovalStep, ExpenseRequest


class ApprovalAuthorizer:
    def can_act(
        self,
        slack_user_id: str,
        request: ExpenseRequest,
        step: ApprovalStep,
    ) -> bool:
        if request.status != RequestStatus.IN_APPROVAL:
            return False
        if request.current_step_order != step.step_order:
            return False
        if step.status != ApprovalStepStatus.PENDING:
            return False
        return slack_user_id in {approver.slack_user_id for approver in step.approvers}
