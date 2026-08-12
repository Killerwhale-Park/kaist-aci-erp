from dataclasses import dataclass

from app.db.enums import EvidenceRequirementLevel, EvidenceTiming


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
class CategorySeed:
    id: str
    name_en: str
    name_ko: str
    evidence: tuple[EvidenceSeed, ...]
    budget_program_id: str = "student_support"


CATEGORY_SEEDS = [
    CategorySeed(
        id="supplies",
        name_en="Supplies",
        name_ko="비품비",
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
    CategorySeed(
        id="lodging",
        name_en="Lodging",
        name_ko="숙박",
        evidence=(
            EvidenceSeed("card_receipt", "Card Receipt", "카드 영수증", EvidenceTiming.PRE),
            EvidenceSeed(
                "proof_of_stay",
                "Proof of Stay",
                "숙박 확인서",
                EvidenceTiming.POST,
                description_en="The proof of stay must include the guest's name.",
                description_ko="숙박 확인서에는 투숙자 실명이 포함되어야 합니다.",
            ),
        ),
    ),
    CategorySeed(
        id="airfare",
        name_en="Airfare",
        name_ko="항공료",
        evidence=(
            EvidenceSeed("card_receipt", "Card Receipt", "카드 영수증", EvidenceTiming.PRE),
            EvidenceSeed("e_ticket", "E-ticket PDF", "E-ticket PDF", EvidenceTiming.PRE),
            EvidenceSeed("boarding_pass", "Boarding Pass", "탑승권", EvidenceTiming.POST),
        ),
    ),
    CategorySeed(
        id="conference_registration",
        name_en="Conference Registration",
        name_ko="학회 참석비",
        evidence=(
            EvidenceSeed(
                "registration_receipt",
                "Registration Receipt",
                "학회 등록 영수증",
                EvidenceTiming.PRE,
                description_en="Include the paid amount and payment details when available.",
                description_ko="가능하면 결제 금액과 결제 정보를 포함해주세요.",
            ),
            EvidenceSeed(
                "conference_badge",
                "Conference Badge",
                "학회 명찰",
                EvidenceTiming.POST,
            ),
        ),
    ),
]
