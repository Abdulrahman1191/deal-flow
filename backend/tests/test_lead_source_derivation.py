"""
Tests for issue #217: deriving `Lead.source` / `source_channel` /
`source_detail` / `applied_at` from a raw Copper lead payload.

Derives from the free-text "Source detail" custom field (id 244394) first --
it's the accurate signal on the 1,143 open Copper leads audited 2026-10-06 --
falling back to the structured `customer_source_id` only when that's blank.
Covers `copper_service.derive_lead_source`/`derive_applied_at` directly, plus
their wiring into `map_copper_lead`.
"""
from __future__ import annotations
from datetime import datetime, timezone

from app.services.copper_service import derive_applied_at, derive_lead_source, map_copper_lead

SOURCE_DETAIL_FIELD_ID = 244394


def _raw(detail: str | None = None, customer_source_id: int | None = None, date_created=None) -> dict:
    raw: dict = {}
    if detail is not None:
        raw["custom_fields"] = [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": detail}]
    if customer_source_id is not None:
        raw["customer_source_id"] = customer_source_id
    if date_created is not None:
        raw["date_created"] = date_created
    return raw


# ---------- derive_lead_source ----------


def test_emailed_info_raed_vc_is_email_inbox():
    result = derive_lead_source(_raw(detail="Emailed info@raed.vc: Investment Opportunity"))
    assert result == {
        "source": "email_inbox",
        "source_channel": None,
        "source_detail": "Emailed info@raed.vc: Investment Opportunity",
    }


def test_leading_emailed_without_inbox_address_is_still_email_inbox():
    result = derive_lead_source(_raw(detail="Emailed: a quick question"))
    assert result["source"] == "email_inbox"
    assert result["source_channel"] is None


def test_website_en_submission_derives_linkedin_channel():
    result = derive_lead_source(_raw(detail="Website (EN) submission: LinkedIn"))
    assert result["source"] == "website_form"
    assert result["source_channel"] == "linkedin"
    assert result["source_detail"] == "Website (EN) submission: LinkedIn"


def test_website_ar_submission_is_website_form():
    result = derive_lead_source(_raw(detail="Website (AR) submission: استفسار"))
    assert result["source"] == "website_form"


def test_website_submission_channel_slug_drops_slash_suffix():
    result = derive_lead_source(_raw(detail="Website (EN) submission: ChatGPT/Claude"))
    assert result["source_channel"] == "chatgpt"


def test_website_submission_channel_slug_drops_second_word():
    result = derive_lead_source(_raw(detail="Website (EN) submission: Google search"))
    assert result["source_channel"] == "google"


def test_unrecognised_wording_is_other():
    result = derive_lead_source(_raw(detail="Referred by a portfolio founder"))
    assert result == {
        "source": "other",
        "source_channel": None,
        "source_detail": "Referred by a portfolio founder",
    }


def test_empty_detail_with_website_customer_source_id_is_website_form():
    result = derive_lead_source(_raw(customer_source_id=1444487))
    assert result == {"source": "website_form", "source_channel": None, "source_detail": None}


def test_empty_detail_with_nothing_is_unknown():
    result = derive_lead_source(_raw())
    assert result == {"source": "unknown", "source_channel": None, "source_detail": None}
    assert derive_lead_source(None) == {"source": "unknown", "source_channel": None, "source_detail": None}


def test_empty_detail_with_unrecognised_customer_source_id_is_unknown():
    result = derive_lead_source(_raw(customer_source_id=999))
    assert result["source"] == "unknown"


def test_matching_is_case_insensitive():
    result = derive_lead_source(_raw(detail="EMAILED INFO@RAED.VC: Hello"))
    assert result["source"] == "email_inbox"


# ---------- derive_applied_at ----------


def test_derive_applied_at_from_epoch_seconds():
    epoch = 1750000000
    assert derive_applied_at({"date_created": epoch}) == datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_derive_applied_at_none_when_missing_or_malformed():
    assert derive_applied_at(None) is None
    assert derive_applied_at({}) is None
    assert derive_applied_at({"date_created": "not-a-timestamp"}) is None
    assert derive_applied_at({"date_created": None}) is None


# ---------- map_copper_lead wiring ----------


def test_map_copper_lead_includes_derived_source_and_applied_at():
    epoch = 1750000000
    p = {
        "id": 1,
        "name": "Founder",
        "custom_fields": [
            {"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "Website (EN) submission: LinkedIn"}
        ],
        "date_created": epoch,
    }
    mapped = map_copper_lead(p)
    assert mapped["source"] == "website_form"
    assert mapped["source_channel"] == "linkedin"
    assert mapped["source_detail"] == "Website (EN) submission: LinkedIn"
    assert mapped["applied_at"] == datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_map_copper_lead_applied_at_none_when_date_created_absent():
    p = {"id": 1, "name": "Founder"}
    mapped = map_copper_lead(p)
    assert mapped["source"] == "unknown"
    assert mapped["applied_at"] is None
