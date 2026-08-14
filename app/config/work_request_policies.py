from dataclasses import dataclass

from app.domain.enums import WorkRequestKind


@dataclass(frozen=True)
class WorkRequestPolicySeed:
    kind: WorkRequestKind
    approval_workflow_id: str | None
    lifecycle_adapter: str


WORK_REQUEST_POLICY_SEEDS = (
    WorkRequestPolicySeed(
        kind=WorkRequestKind.PURCHASE,
        approval_workflow_id="purchase_payment_approval",
        lifecycle_adapter="purchase_to_settlement",
    ),
    WorkRequestPolicySeed(
        kind=WorkRequestKind.SETTLEMENT,
        approval_workflow_id=None,
        lifecycle_adapter="settlement_to_expense",
    ),
)


def work_request_policy(kind: WorkRequestKind) -> WorkRequestPolicySeed:
    return next(item for item in WORK_REQUEST_POLICY_SEEDS if item.kind == kind)
