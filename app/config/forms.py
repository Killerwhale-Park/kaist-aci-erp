from dataclasses import dataclass

from app.domain.enums import EvidenceRequirementLevel, EvidenceTiming


@dataclass(frozen=True)
class EvidenceSeed:
    key: str
    name_en: str
    name_ko: str
    timing: EvidenceTiming
    requirement: EvidenceRequirementLevel = EvidenceRequirementLevel.OPTIONAL
    allow_waiver: bool = False
    description_en: str | None = None
    description_ko: str | None = None


@dataclass(frozen=True)
class ExpenseFormSeed:
    id: str
    name_en: str
    name_ko: str
    evidence: tuple[EvidenceSeed, ...]


EXPENSE_FORM_SEEDS = [
    ExpenseFormSeed(
        id="supplies_settlement",
        name_en="Supplies Settlement Form",
        name_ko="비품비 정산 양식",
        evidence=(
            EvidenceSeed("card_receipt", "Card Receipt", "카드 영수증", EvidenceTiming.PRE),
            EvidenceSeed("item_photo", "Item Photo", "물품 사진", EvidenceTiming.PRE),
            EvidenceSeed(
                "product_page_screenshot",
                "Product Page Screenshot",
                "물품 판매 페이지 캡처",
                EvidenceTiming.PRE,
            ),
        ),
    ),
]
