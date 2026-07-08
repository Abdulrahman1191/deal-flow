"""
Tests for the YES-bucket leads CSV export (GET /api/v1/leads/export).

The CSV rendering and bucket-resolution logic is factored into
app.services.csv_export so it's testable without a live database — the
router itself just queries leads and hands rows to build_leads_csv.
"""
from types import SimpleNamespace

from app.services.csv_export import LEADS_CSV_HEADERS, build_leads_csv, effective_bucket


def test_empty_export_returns_headers_only():
    csv_text = build_leads_csv([])
    lines = csv_text.strip("\r\n").split("\r\n")

    assert len(lines) == 1
    assert lines[0] == ",".join(LEADS_CSV_HEADERS)


def test_export_includes_matching_rows():
    rows = [
        {
            "company_name": "Acme Deep Tech",
            "bucket": "YES",
            "confidence_score": 88,
            "created_date": "2026-07-01T00:00:00+00:00",
        }
    ]
    csv_text = build_leads_csv(rows)
    lines = csv_text.strip("\r\n").split("\r\n")

    assert lines[0] == ",".join(LEADS_CSV_HEADERS)
    assert lines[1] == "Acme Deep Tech,YES,88,2026-07-01T00:00:00+00:00"


def test_export_quotes_company_names_with_commas():
    rows = [
        {
            "company_name": "Acme, Inc.",
            "bucket": "YES",
            "confidence_score": 90,
            "created_date": "2026-07-01T00:00:00+00:00",
        }
    ]
    csv_text = build_leads_csv(rows)

    assert '"Acme, Inc."' in csv_text


def test_effective_bucket_prefers_user_override():
    assessment = SimpleNamespace(bucket="MAYBE", user_override="YES")
    assert effective_bucket(assessment) == "YES"


def test_effective_bucket_falls_back_to_ai_bucket():
    assessment = SimpleNamespace(bucket="YES", user_override=None)
    assert effective_bucket(assessment) == "YES"


def test_effective_bucket_none_when_no_assessment():
    assert effective_bucket(None) is None
