from dataclasses import dataclass

from app.application.work_items import UserWorkQueue, build_user_work_queue
from app.config.roles import (
    ASSIGN_SETTLEMENT,
    MANAGE_CONFIGURATION,
    SUBMIT_REQUEST,
    assigned_users_with_capability,
)
from app.domain.models import ApplicantProfile


@dataclass(frozen=True)
class DashboardCapabilities:
    can_request: bool
    expense_ready: bool
    purchase_ready: bool
    can_assign_settlement: bool
    can_manage_configuration: bool


@dataclass(frozen=True)
class UserDashboard:
    slack_user_id: str
    applicant_profile: ApplicantProfile | None
    work_queue: UserWorkQueue
    capabilities: DashboardCapabilities


@dataclass(frozen=True)
class DashboardData:
    assignments: dict[str, dict[str, set[str]]]
    submission_configuration: tuple[bool, bool]
    applicant_profile: ApplicantProfile | None
    own_expenses: list
    pending_expense_approvals: list
    submitted_work_requests: list
    actionable_work_requests: list


async def load_user_dashboard(repository, slack_user_id: str) -> UserDashboard:
    """Build one user-facing projection without coupling it to Slack blocks."""

    data: DashboardData = await repository.dashboard_data(slack_user_id)
    admins = assigned_users_with_capability(data.assignments, MANAGE_CONFIGURATION)
    settlement_assigners = assigned_users_with_capability(data.assignments, ASSIGN_SETTLEMENT)
    requesters = assigned_users_with_capability(data.assignments, SUBMIT_REQUEST)
    purchase_ready, expense_ready = data.submission_configuration
    return UserDashboard(
        slack_user_id=slack_user_id,
        applicant_profile=data.applicant_profile,
        work_queue=build_user_work_queue(
            slack_user_id,
            own_expenses=data.own_expenses,
            pending_expense_approvals=data.pending_expense_approvals,
            submitted_work_requests=data.submitted_work_requests,
            actionable_work_requests=data.actionable_work_requests,
        ),
        capabilities=DashboardCapabilities(
            can_request=slack_user_id in requesters,
            expense_ready=expense_ready,
            purchase_ready=purchase_ready,
            can_assign_settlement=slack_user_id in settlement_assigners,
            can_manage_configuration=slack_user_id in admins,
        ),
    )
