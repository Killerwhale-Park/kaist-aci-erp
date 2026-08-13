from urllib.parse import urlparse

from app.domain.enums import (
    EvidenceRequirementLevel,
    EvidenceSubmissionStatus,
    EvidenceTiming,
)
from app.domain.models import EvidenceSubmission
from app.exceptions import DomainValidationError
from app.i18n import t

GOOGLE_EVIDENCE_HOSTS = {"drive.google.com", "docs.google.com"}


def validate_https_url(url: str | None, field_name: str) -> None:
    if url is None:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise DomainValidationError(
            "Evidence links must be valid HTTPS URLs",
            {field_name: t("https_required")},
        )


def is_google_evidence_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname in GOOGLE_EVIDENCE_HOSTS or any(
        hostname.endswith(f".{host}") for host in GOOGLE_EVIDENCE_HOSTS
    )


def drive_warning_urls(urls: list[str | None]) -> list[str]:
    return [url for url in urls if url and not is_google_evidence_url(url)]


def validate_required_evidence(
    submissions: list[EvidenceSubmission], timing: EvidenceTiming
) -> None:
    missing = [
        submission.requirement_key
        for submission in submissions
        if submission.timing == timing
        and submission.requirement == EvidenceRequirementLevel.REQUIRED
        and not submission.url
    ]
    if missing:
        raise DomainValidationError(
            "Required evidence is missing",
            {f"evidence__{key}": t("required_evidence") for key in missing},
        )


def required_post_evidence_complete(submissions: list[EvidenceSubmission]) -> bool:
    return all(
        submission.url
        for submission in submissions
        if submission.timing == EvidenceTiming.POST
        and submission.requirement == EvidenceRequirementLevel.REQUIRED
    )


def apply_evidence_value(submission: EvidenceSubmission, url: str | None, note: str | None) -> bool:
    from datetime import UTC, datetime

    value_changed = submission.url != url or submission.note != note
    submission.url = url
    submission.note = note
    submission.status = (
        EvidenceSubmissionStatus.SUBMITTED if url else EvidenceSubmissionStatus.MISSING
    )
    submission.submitted_at = datetime.now(UTC) if url else None
    return bool(url) and value_changed
