import pytest

from app.exceptions import DomainValidationError
from app.expenses.evidence import drive_warning_urls, validate_https_url


def test_evidence_url_must_use_https() -> None:
    with pytest.raises(DomainValidationError):
        validate_https_url("http://drive.google.com/file/example", "evidence__receipt")

    validate_https_url("https://drive.google.com/file/example", "evidence__receipt")


def test_non_google_https_url_is_warning_only() -> None:
    url = "https://example.edu/evidence/receipt.pdf"

    validate_https_url(url, "evidence__receipt")
    assert drive_warning_urls([url]) == [url]
    assert drive_warning_urls(["https://drive.google.com/file/example"]) == []
