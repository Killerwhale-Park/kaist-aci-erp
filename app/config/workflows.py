from dataclasses import dataclass

from app.config.roles import ADMIN_STAFF_ROLE, PROFESSOR_ROLE, STUDENT_COORDINATOR_ROLE


@dataclass(frozen=True)
class ApprovalStepSeed:
    id: str
    name_en: str
    name_ko: str
    approver_roles: tuple[str, ...] = ()
    actor_binding: str | None = None


@dataclass(frozen=True)
class ApprovalWorkflowSeed:
    id: str
    name_en: str
    name_ko: str
    steps: tuple[ApprovalStepSeed, ...]


APPROVAL_WORKFLOW_SEEDS = [
    ApprovalWorkflowSeed(
        id="academic_development_approval",
        name_en="Academic Development Approval",
        name_ko="학사계발비 승인",
        steps=(
            ApprovalStepSeed(
                id="student_coordinator_review",
                name_en="Student Coordinator Review",
                name_ko="학생 담당자 확인",
                approver_roles=(STUDENT_COORDINATOR_ROLE,),
            ),
            ApprovalStepSeed(
                id="responsible_professor_approval",
                name_en="Responsible Professor Approval and Inspection",
                name_ko="담당 교수 승인 및 검수",
                approver_roles=(PROFESSOR_ROLE,),
            ),
            ApprovalStepSeed(
                id="administrative_review",
                name_en="Administrative Review",
                name_ko="행정팀 검토",
                approver_roles=(ADMIN_STAFF_ROLE,),
            ),
        ),
    ),
    ApprovalWorkflowSeed(
        id="purchase_payment_approval",
        name_en="Purchase Payment Approval",
        name_ko="구매 결제 승인",
        steps=(
            ApprovalStepSeed(
                id="payment_assignee_approval",
                name_en="Payment Assignee Approval",
                name_ko="결제 담당자 승인",
                approver_roles=(PROFESSOR_ROLE,),
                actor_binding="payment_assignee",
            ),
        ),
    ),
]
