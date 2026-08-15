from enum import StrEnum


class BudgetFormScope(StrEnum):
    DEPARTMENT = "DEPARTMENT"
    GLOBAL = "GLOBAL"


class ApplicantType(StrEnum):
    STUDENT = "STUDENT"
    PROFESSOR = "PROFESSOR"
    # Kept only for replaying records created before applicant types were clarified.
    OTHER = "OTHER"


class EvidenceTiming(StrEnum):
    PRE = "PRE"
    POST = "POST"


class EvidenceRequirementLevel(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class EvidenceSubmissionStatus(StrEnum):
    MISSING = "MISSING"
    SUBMITTED = "SUBMITTED"


class ApprovalStepStatus(StrEnum):
    WAITING = "WAITING"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"


class RequestStatus(StrEnum):
    IN_APPROVAL = "IN_APPROVAL"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"
    APPROVED_PENDING_POST_EVIDENCE = "APPROVED_PENDING_POST_EVIDENCE"
    COMPLETED = "COMPLETED"


class WorkRequestKind(StrEnum):
    PURCHASE = "PURCHASE"
    SETTLEMENT = "SETTLEMENT"


class WorkRequestStatus(StrEnum):
    IN_APPROVAL = "IN_APPROVAL"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    REJECTED = "REJECTED"
    # Retained for replaying work requests created before approval chains were introduced.
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
