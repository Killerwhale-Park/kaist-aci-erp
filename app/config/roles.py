from dataclasses import dataclass

WORKSPACE_ROLE_SCOPE = "workspace"

STUDENT_COORDINATOR_ROLE = "STUDENT_COORDINATOR"
PROFESSOR_ROLE = "PROFESSOR"
ADMIN_STAFF_ROLE = "ADMIN_STAFF"
SYSTEM_ADMIN_ROLE = "SYSTEM_ADMIN"

ASSIGN_SETTLEMENT = "ASSIGN_SETTLEMENT"
MANAGE_CONFIGURATION = "MANAGE_CONFIGURATION"


@dataclass(frozen=True)
class RoleDefinitionSeed:
    id: str
    name_en: str
    name_ko: str
    capabilities: frozenset[str] = frozenset()
    required: bool = False


@dataclass(frozen=True)
class RoleAssignmentSeed:
    slack_user_id: str
    role_ids: frozenset[str]


ROLE_DEFINITION_SEEDS = (
    RoleDefinitionSeed(
        STUDENT_COORDINATOR_ROLE,
        "Student Coordinators",
        "학생 담당자",
    ),
    RoleDefinitionSeed(
        PROFESSOR_ROLE,
        "Professors",
        "교수",
        capabilities=frozenset({ASSIGN_SETTLEMENT}),
    ),
    RoleDefinitionSeed(
        ADMIN_STAFF_ROLE,
        "Administrative Staff",
        "행정팀",
        capabilities=frozenset({ASSIGN_SETTLEMENT}),
    ),
    RoleDefinitionSeed(
        SYSTEM_ADMIN_ROLE,
        "System Administrators",
        "시스템 관리자",
        capabilities=frozenset({ASSIGN_SETTLEMENT, MANAGE_CONFIGURATION}),
        required=True,
    ),
)

ROLE_ASSIGNMENT_SEEDS = (
    RoleAssignmentSeed(
        slack_user_id="U0BGPFFNR6W",
        role_ids=frozenset({SYSTEM_ADMIN_ROLE}),
    ),
)


def role_definitions() -> tuple[RoleDefinitionSeed, ...]:
    return ROLE_DEFINITION_SEEDS


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
        for role_id in seed.role_ids:
            assignments[WORKSPACE_ROLE_SCOPE][role_id].add(seed.slack_user_id)
    return assignments
