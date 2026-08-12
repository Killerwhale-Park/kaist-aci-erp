from dataclasses import dataclass

from app.config.settings import Settings


@dataclass(frozen=True)
class DepartmentSeed:
    id: str
    name_en: str
    name_ko: str
    approval_channel_id: str


def department_seeds(settings: Settings) -> list[DepartmentSeed]:
    return [
        DepartmentSeed(
            id=f"department_{index}",
            name_en=f"Department {index}",
            name_ko=f"학과 {index}",
            approval_channel_id=settings.approval_channel_for(f"department_{index}"),
        )
        for index in range(1, 5)
    ]
