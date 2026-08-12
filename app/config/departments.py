from dataclasses import dataclass


@dataclass(frozen=True)
class DepartmentSeed:
    id: str
    name_en: str
    name_ko: str
    approval_channel_id: str | None = None


def department_seeds() -> list[DepartmentSeed]:
    return [
        DepartmentSeed(
            id=f"department_{index}",
            name_en=f"Department {index}",
            name_ko=f"학과 {index}",
        )
        for index in range(1, 5)
    ]
