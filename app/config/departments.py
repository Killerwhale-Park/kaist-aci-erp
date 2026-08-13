from dataclasses import dataclass


@dataclass(frozen=True)
class DepartmentSeed:
    id: str
    name_en: str
    name_ko: str


def department_seeds() -> list[DepartmentSeed]:
    return [
        DepartmentSeed("department_1", "AI Computing", "AI Computing"),
        DepartmentSeed("department_2", "AI System", "AI System"),
        DepartmentSeed("department_3", "AX", "AX"),
        DepartmentSeed("department_4", "AI Future", "AI Future"),
    ]
