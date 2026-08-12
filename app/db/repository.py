import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ExpenseRequest
from app.exceptions import EntityNotFoundError

REQUEST_LOAD_OPTIONS = (
    selectinload(ExpenseRequest.evidence_submissions),
    selectinload(ExpenseRequest.approval_steps),
    selectinload(ExpenseRequest.department),
    selectinload(ExpenseRequest.budget_program),
    selectinload(ExpenseRequest.category),
)


class ExpenseRequestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, request_id: uuid.UUID | str, *, for_update: bool = False) -> ExpenseRequest:
        statement = select(ExpenseRequest).where(ExpenseRequest.id == uuid.UUID(str(request_id)))
        if for_update:
            statement = statement.with_for_update()
        statement = statement.options(*REQUEST_LOAD_OPTIONS)
        request = self.session.scalar(statement)
        if request is None:
            raise EntityNotFoundError("Expense request not found")
        return request

    def list_for_applicant(self, slack_user_id: str, limit: int = 10) -> list[ExpenseRequest]:
        statement = (
            select(ExpenseRequest)
            .where(ExpenseRequest.applicant_slack_user_id == slack_user_id)
            .order_by(ExpenseRequest.created_at.desc())
            .limit(limit)
            .options(*REQUEST_LOAD_OPTIONS)
        )
        return list(self.session.scalars(statement).unique())

    def list_in_approval(self, limit: int = 100) -> list[ExpenseRequest]:
        from app.db.enums import RequestStatus

        statement = (
            select(ExpenseRequest)
            .where(ExpenseRequest.status == RequestStatus.IN_APPROVAL)
            .order_by(ExpenseRequest.submitted_at.asc())
            .limit(limit)
            .options(*REQUEST_LOAD_OPTIONS)
        )
        return list(self.session.scalars(statement).unique())
