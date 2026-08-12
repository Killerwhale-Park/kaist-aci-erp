class DomainError(Exception):
    pass


class ConfigurationError(DomainError):
    pass


class EntityNotFoundError(DomainError):
    pass


class DomainValidationError(DomainError):
    def __init__(self, message: str, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.field_errors = field_errors or {}


class ApprovalPermissionError(DomainError):
    pass


class InvalidStateTransitionError(DomainError):
    pass


class AuditLogMutationError(DomainError):
    pass
