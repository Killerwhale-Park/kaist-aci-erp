from dataclasses import dataclass

from app.config.settings import Settings
from app.db.enums import ApproverType


@dataclass(frozen=True)
class ApprovalStepSeed:
    name_en: str
    name_ko: str
    approver_type: ApproverType
    approver_reference: str
    required: bool = True


@dataclass(frozen=True)
class WorkflowRuleSeed:
    workflow_id: str
    rule_id: str
    department_id: str
    budget_program_id: str
    category_id: str
    name_en: str
    name_ko: str
    steps: tuple[ApprovalStepSeed, ...]


def workflow_rule_seeds(settings: Settings) -> list[WorkflowRuleSeed]:
    rules: list[WorkflowRuleSeed] = []
    for department_number in range(1, 5):
        department_id = f"department_{department_number}"
        professor = settings.approver_for(department_id, "professor")
        administration = settings.approver_for(department_id, "administration")
        inspector = settings.approver_for(department_id, "inspector")
        for category_id in (
            "supplies",
            "lodging",
            "airfare",
            "conference_registration",
        ):
            steps = [
                ApprovalStepSeed(
                    "Professor Approval",
                    "교수 승인",
                    ApproverType.SLACK_USER,
                    professor,
                ),
                ApprovalStepSeed(
                    "Administration Review",
                    "행정 검토",
                    ApproverType.SLACK_USER,
                    administration,
                ),
            ]
            if category_id == "supplies":
                steps.append(
                    ApprovalStepSeed(
                        "Inspection",
                        "검수",
                        ApproverType.SLACK_USER,
                        inspector,
                    )
                )
            workflow_id = f"wf_{department_id}_{category_id}_v1"
            rules.append(
                WorkflowRuleSeed(
                    workflow_id=workflow_id,
                    rule_id=f"rule_{department_id}_student_support_{category_id}",
                    department_id=department_id,
                    budget_program_id="student_support",
                    category_id=category_id,
                    name_en=f"{department_id} {category_id} sample workflow",
                    name_ko=f"{department_id} {category_id} 샘플 승인 절차",
                    steps=tuple(steps),
                )
            )
    return rules
