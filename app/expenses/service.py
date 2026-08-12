import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.approvals.engine import ApprovalEngine
from app.approvals.resolver import ApprovalRuleResolver
from app.db.enums import (
    ApprovalStepStatus,
    AuditEventType,
    EvidenceSubmissionStatus,
    EvidenceTiming,
    RequestStatus,
)
from app.db.models import (
    ApprovalActionLog,
    ApprovalStep,
    ApprovalStepApprover,
    BudgetProgram,
    Department,
    EvidenceSubmission,
    ExpenseCategory,
    ExpenseRequest,
)
from app.db.repository import ExpenseRequestRepository
from app.exceptions import (
    ConfigurationError,
    DomainValidationError,
    InvalidStateTransitionError,
)
from app.expenses.evidence import (
    apply_evidence_value,
    validate_https_url,
    validate_required_evidence,
)
from app.expenses.schemas import CreateExpenseCommand, EditExpenseCommand, PostEvidenceCommand
from app.i18n import t
from app.users.service import UserProfileService


class ExpenseService:
    def __init__(
        self,
        session: Session,
        user_profiles: UserProfileService,
        engine: ApprovalEngine | None = None,
    ) -> None:
        self.session = session
        self.user_profiles = user_profiles
        self.engine = engine or ApprovalEngine()
        self.resolver = ApprovalRuleResolver(session)
        self.repository = ExpenseRequestRepository(session)

    def create_and_submit(self, command: CreateExpenseCommand) -> ExpenseRequest:
        department, budget, category = self._validate_configuration(command)
        workflow = self.resolver.resolve_workflow(
            command.department_id, command.budget_program_id, command.category_id
        )
        requirements = self.resolver.evidence_requirements(command.category_id)
        self._validate_urls(command.evidence_folder_url, command.evidence)

        self.user_profiles.update_applicant_details(
            slack_user_id=command.applicant_slack_user_id,
            display_name=command.applicant_display_name,
            department_id=command.department_id,
            applicant_type=command.applicant_type,
            student_id=command.student_id,
        )

        request_id = uuid.uuid4()
        request = ExpenseRequest(
            id=request_id,
            reference_number=self._reference_number(request_id),
            applicant_slack_user_id=command.applicant_slack_user_id,
            applicant_display_name=command.applicant_display_name,
            applicant_type=command.applicant_type,
            student_id=command.student_id,
            department_id=department.id,
            budget_program_id=budget.id,
            category_id=category.id,
            amount=command.amount,
            currency=command.currency,
            vendor=command.vendor,
            payment_date=command.payment_date,
            purpose=command.purpose,
            evidence_folder_url=command.evidence_folder_url,
            status=RequestStatus.DRAFT,
            approval_channel_id=department.approval_channel_id,
            workflow_snapshot=[
                {
                    "definition_id": step.id,
                    "order": step.step_order,
                    "name_en": step.name_en,
                    "name_ko": step.name_ko,
                    "approval_policy": step.approval_policy.value,
                    "approver_slack_user_ids": [
                        approver.slack_user_id for approver in step.approvers
                    ],
                    "required": step.required,
                }
                for step in sorted(workflow.steps, key=lambda item: item.step_order)
            ],
            evidence_snapshot=[
                {
                    "definition_id": requirement.id,
                    "key": requirement.evidence_key,
                    "name_en": requirement.name_en,
                    "name_ko": requirement.name_ko,
                    "timing": requirement.timing.value,
                    "requirement": requirement.requirement.value,
                    "allow_waiver": requirement.allow_waiver,
                    "description_en": requirement.description_en,
                    "description_ko": requirement.description_ko,
                }
                for requirement in requirements
            ],
        )

        for requirement in requirements:
            provided = command.evidence.get(requirement.evidence_key)
            submission = EvidenceSubmission(
                requirement_definition_id=requirement.id,
                requirement_key=requirement.evidence_key,
                name_en=requirement.name_en,
                name_ko=requirement.name_ko,
                timing=requirement.timing,
                requirement=requirement.requirement,
                allow_waiver=requirement.allow_waiver,
                description_en=requirement.description_en,
                description_ko=requirement.description_ko,
                display_order=requirement.display_order,
                url=provided.url if provided else None,
                note=provided.note if provided else None,
                status=(
                    EvidenceSubmissionStatus.SUBMITTED
                    if provided and provided.url
                    else EvidenceSubmissionStatus.MISSING
                ),
                submitted_at=(datetime.now(UTC) if provided and provided.url else None),
            )
            request.evidence_submissions.append(submission)

        for definition in sorted(workflow.steps, key=lambda item: item.step_order):
            step = ApprovalStep(
                step_definition_id=definition.id,
                step_order=definition.step_order,
                name_en=definition.name_en,
                name_ko=definition.name_ko,
                approval_policy=definition.approval_policy,
                required=definition.required,
                status=ApprovalStepStatus.WAITING,
            )
            step.approvers.extend(
                ApprovalStepApprover(slack_user_id=approver.slack_user_id)
                for approver in definition.approvers
            )
            request.approval_steps.append(step)

        validate_required_evidence(request.evidence_submissions, EvidenceTiming.PRE)
        self.engine.initialize(request)
        self.session.add(request)
        self.session.flush()
        self._add_audit(request, AuditEventType.REQUEST_CREATED, command.applicant_slack_user_id)
        self._add_audit(request, AuditEventType.REQUEST_SUBMITTED, command.applicant_slack_user_id)
        submitted_keys = [item.requirement_key for item in request.evidence_submissions if item.url]
        if submitted_keys:
            self._add_audit(
                request,
                AuditEventType.EVIDENCE_SUBMITTED,
                command.applicant_slack_user_id,
                metadata={"requirement_keys": submitted_keys, "timing": EvidenceTiming.PRE.value},
            )
        self.session.flush()
        return request

    def resubmit(
        self,
        request_id: uuid.UUID | str,
        actor_slack_user_id: str,
        command: EditExpenseCommand,
    ) -> ExpenseRequest:
        request = self.repository.get(request_id, for_update=True)
        self._assert_applicant(request, actor_slack_user_id)
        if request.status != RequestStatus.CHANGES_REQUESTED:
            raise InvalidStateTransitionError(
                "Only a returned request can be edited and resubmitted"
            )
        self._validate_urls(command.evidence_folder_url, command.evidence)

        pre_submissions = [
            submission
            for submission in request.evidence_submissions
            if submission.timing == EvidenceTiming.PRE
        ]
        for submission in pre_submissions:
            provided = command.evidence.get(submission.requirement_key)
            prospective_url = provided.url if provided else submission.url
            if not prospective_url and submission.requirement.value == "REQUIRED":
                raise DomainValidationError(
                    "Required evidence is missing",
                    {f"evidence__{submission.requirement_key}": t("required_evidence")},
                )

        request.amount = command.amount
        request.vendor = command.vendor
        request.payment_date = command.payment_date
        request.purpose = command.purpose
        request.evidence_folder_url = command.evidence_folder_url
        submitted_keys: list[str] = []
        for submission in pre_submissions:
            provided = command.evidence.get(submission.requirement_key)
            if provided and apply_evidence_value(submission, provided.url, provided.note):
                submitted_keys.append(submission.requirement_key)

        validate_required_evidence(request.evidence_submissions, EvidenceTiming.PRE)
        resumed_step = self.engine.resubmit(request)
        self._add_audit(
            request,
            AuditEventType.REQUEST_RESUBMITTED,
            actor_slack_user_id,
            step=resumed_step,
            metadata={"revision": request.revision},
        )
        if submitted_keys:
            self._add_audit(
                request,
                AuditEventType.EVIDENCE_SUBMITTED,
                actor_slack_user_id,
                metadata={"requirement_keys": submitted_keys, "timing": EvidenceTiming.PRE.value},
            )
        self.session.flush()
        return request

    def submit_post_evidence(
        self,
        request_id: uuid.UUID | str,
        actor_slack_user_id: str,
        command: PostEvidenceCommand,
    ) -> ExpenseRequest:
        request = self.repository.get(request_id, for_update=True)
        self._assert_applicant(request, actor_slack_user_id)
        if request.status not in {
            RequestStatus.APPROVED_PENDING_POST_EVIDENCE,
            RequestStatus.COMPLETED,
        }:
            raise InvalidStateTransitionError("This request is not awaiting post-event evidence")
        self._validate_urls(None, command.evidence)
        if not any(item.url for item in command.evidence.values()):
            first_key = next(iter(command.evidence), "post_evidence")
            raise DomainValidationError(
                "At least one evidence URL is required",
                {f"evidence__{first_key}": t("post_evidence_one")},
            )

        post_by_key = {
            item.requirement_key: item
            for item in request.evidence_submissions
            if item.timing == EvidenceTiming.POST
        }
        submitted_keys: list[str] = []
        for key, provided in command.evidence.items():
            submission = post_by_key.get(key)
            if submission is None:
                raise DomainValidationError("Unknown post-event evidence type")
            if apply_evidence_value(submission, provided.url, provided.note):
                submitted_keys.append(key)

        self._add_audit(
            request,
            AuditEventType.POST_EVIDENCE_SUBMITTED,
            actor_slack_user_id,
            metadata={"requirement_keys": submitted_keys},
        )
        previous_status = request.status
        if previous_status == RequestStatus.APPROVED_PENDING_POST_EVIDENCE:
            self.engine.evaluate_post_evidence(request)
        if previous_status != request.status and request.status == RequestStatus.COMPLETED:
            self._add_audit(request, AuditEventType.REQUEST_COMPLETED, actor_slack_user_id)
        self.session.flush()
        return request

    def _validate_configuration(
        self, command: CreateExpenseCommand
    ) -> tuple[Department, BudgetProgram, ExpenseCategory]:
        department = self.session.get(Department, command.department_id)
        budget = self.session.get(BudgetProgram, command.budget_program_id)
        category = self.session.get(ExpenseCategory, command.category_id)
        if department is None or not department.is_active:
            raise ConfigurationError("The selected department is unavailable")
        if not department.approval_channel_id:
            raise ConfigurationError("The selected department has no approval channel")
        if budget is None or not budget.is_active or not budget.is_available:
            raise ConfigurationError("The selected budget is unavailable")
        if category is None or not category.is_active:
            raise ConfigurationError("The selected expense category is unavailable")
        if category.budget_program_id != budget.id:
            raise ConfigurationError("The selected category does not belong to this budget")
        return department, budget, category

    def _validate_urls(self, folder_url: str | None, evidence: dict) -> None:
        validate_https_url(folder_url, "evidence_folder")
        for key, value in evidence.items():
            validate_https_url(value.url, f"evidence__{key}")

    def _assert_applicant(self, request: ExpenseRequest, actor_slack_user_id: str) -> None:
        if request.applicant_slack_user_id != actor_slack_user_id:
            from app.exceptions import ApprovalPermissionError

            raise ApprovalPermissionError("Only the applicant can update this request")

    def _add_audit(
        self,
        request: ExpenseRequest,
        event_type: AuditEventType,
        actor_slack_user_id: str | None,
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

    def _reference_number(self, request_id: uuid.UUID) -> str:
        year = datetime.now(UTC).year
        return f"EXP-{year}-{request_id.hex[:8].upper()}"
