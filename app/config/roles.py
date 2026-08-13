from dataclasses import dataclass

WORKSPACE_ROLE_SCOPE = "workspace"

REQUESTER_ROLE = "REQUESTER"
STUDENT_COORDINATOR_ROLE = "STUDENT_COORDINATOR"
PROFESSOR_ROLE = "PROFESSOR"
ADMIN_STAFF_ROLE = "ADMIN_STAFF"
SYSTEM_ADMIN_ROLE = "SYSTEM_ADMIN"

SUBMIT_REQUEST = "SUBMIT_REQUEST"
ASSIGN_SETTLEMENT = "ASSIGN_SETTLEMENT"
MANAGE_CONFIGURATION = "MANAGE_CONFIGURATION"


@dataclass(frozen=True)
class RoleDefinitionSeed:
    id: str
    name_en: str
    name_ko: str
    department_scoped: bool
    capabilities: frozenset[str] = frozenset()
    required: bool = False


@dataclass(frozen=True)
class RoleAssignmentSeed:
    slack_user_id: str
    role_ids: frozenset[str]
    department_id: str | None = None


ROLE_DEFINITION_SEEDS = (
    RoleDefinitionSeed(
        REQUESTER_ROLE,
        "Eligible Requesters",
        "신청 가능자",
        department_scoped=True,
        capabilities=frozenset({SUBMIT_REQUEST}),
    ),
    RoleDefinitionSeed(
        STUDENT_COORDINATOR_ROLE,
        "Student Coordinators",
        "학생 담당자",
        department_scoped=True,
        capabilities=frozenset({SUBMIT_REQUEST}),
    ),
    RoleDefinitionSeed(
        PROFESSOR_ROLE,
        "Professors",
        "교수",
        department_scoped=True,
        capabilities=frozenset({SUBMIT_REQUEST, ASSIGN_SETTLEMENT}),
    ),
    RoleDefinitionSeed(
        ADMIN_STAFF_ROLE,
        "Administrative Staff",
        "행정팀",
        department_scoped=True,
        capabilities=frozenset({SUBMIT_REQUEST, ASSIGN_SETTLEMENT}),
    ),
    RoleDefinitionSeed(
        SYSTEM_ADMIN_ROLE,
        "System Administrators",
        "시스템 관리자",
        department_scoped=False,
        capabilities=frozenset({SUBMIT_REQUEST, ASSIGN_SETTLEMENT, MANAGE_CONFIGURATION}),
        required=True,
    ),
)

ROLE_ASSIGNMENT_SEEDS = (
    RoleAssignmentSeed(
        slack_user_id="U0BGPFFNR6W",
        role_ids=frozenset({SYSTEM_ADMIN_ROLE}),
    ),
)


def role_definitions(*, department_scoped: bool | None = None) -> tuple[RoleDefinitionSeed, ...]:
    if department_scoped is None:
        return ROLE_DEFINITION_SEEDS
    return tuple(
        definition
        for definition in ROLE_DEFINITION_SEEDS
        if definition.department_scoped == department_scoped
    )


def role_ids() -> tuple[str, ...]:
    return tuple(definition.id for definition in ROLE_DEFINITION_SEEDS)


def roles_with_capability(capability: str) -> tuple[str, ...]:
    return tuple(
        definition.id
        for definition in ROLE_DEFINITION_SEEDS
        if capability in definition.capabilities
    )


def empty_role_set() -> dict[str, set[str]]:
    return {role_id: set() for role_id in role_ids()}


def default_role_assignments() -> dict[str, dict[str, set[str]]]:
    assignments = {WORKSPACE_ROLE_SCOPE: empty_role_set()}
    for seed in ROLE_ASSIGNMENT_SEEDS:
        scope = seed.department_id or WORKSPACE_ROLE_SCOPE
        scoped = assignments.setdefault(scope, empty_role_set())
        for role_id in seed.role_ids:
            scoped[role_id].add(seed.slack_user_id)
    return assignments
