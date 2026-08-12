from app.db.enums import ApprovalStepStatus, ApproverType, RequestStatus, UserRole
from app.db.models import ApprovalStep, ExpenseRequest, UserProfile


class ApprovalAuthorizer:
    def can_act(
        self,
        slack_user_id: str,
        request: ExpenseRequest,
        step: ApprovalStep,
        profile: UserProfile | None,
    ) -> bool:
        if request.status != RequestStatus.IN_APPROVAL:
            return False
        if request.current_step_order != step.step_order:
            return False
        if step.status != ApprovalStepStatus.PENDING:
            return False
        if step.approver_type == ApproverType.SLACK_USER:
            return step.approver_reference == slack_user_id
        if step.approver_type == ApproverType.DEPARTMENT_ROLE:
            return bool(
                profile
                and profile.department_id == request.department_id
                and profile.role.value == step.approver_reference
                and profile.role in {UserRole.APPROVER, UserRole.SYSTEM_ADMIN}
            )
        return False
