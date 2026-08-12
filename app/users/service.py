from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.enums import ApplicantType, UserRole
from app.db.models import UserProfile


class UserProfileService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def get_or_create(self, slack_user_id: str, display_name: str | None = None) -> UserProfile:
        profile = self.session.get(UserProfile, slack_user_id)
        if profile is None:
            profile = UserProfile(
                slack_user_id=slack_user_id,
                display_name=display_name or slack_user_id,
                role=UserRole.REQUESTER,
            )
            self.session.add(profile)
        elif display_name:
            profile.display_name = display_name
        return profile

    def update_applicant_details(
        self,
        slack_user_id: str,
        display_name: str,
        department_id: str,
        applicant_type: ApplicantType,
        student_id: str | None,
    ) -> UserProfile:
        profile = self.get_or_create(slack_user_id, display_name)
        profile.applicant_type = applicant_type
        profile.student_id = student_id
        if profile.role == UserRole.REQUESTER:
            profile.department_id = department_id
        return profile
