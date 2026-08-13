from enum import StrEnum


class UserRole(StrEnum):
    REQUESTER = "REQUESTER"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


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
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
