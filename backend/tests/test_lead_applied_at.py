"""
Tests for LeadOut.applied_at — the lead card's "when did they apply" date.

It should prefer the true Copper application date (raw_copper_data.date_created,
epoch seconds) and fall back to our own created_at (import timestamp) whenever
that data is missing or malformed, without ever raising.
"""
import uuid
from datetime import datetime, timezone

from app.schemas.lead import LeadOut


def _base_kwargs(**overrides):
    kwargs = dict(
        id=uuid.uuid4(),
        copper_id="123",
        owner_email="founder@raed.vc",
        company_name="Acme Deep Tech",
        website=None,
        description=None,
        stage=None,
        region=None,
        founder_names=None,
        linkedin_urls=None,
        company_linkedin_url=None,
        status="pending",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_copper_data=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_applied_at_derives_from_copper_date_created():
    epoch_seconds = 1750000000  # 2025-06-15T16:26:40Z
    lead = LeadOut(**_base_kwargs(raw_copper_data={"date_created": epoch_seconds}))

    assert lead.applied_at == datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


def test_applied_at_falls_back_to_created_at_when_raw_copper_data_is_none():
    lead = LeadOut(**_base_kwargs(raw_copper_data=None))

    assert lead.applied_at == lead.created_at


def test_applied_at_falls_back_to_created_at_when_key_missing():
    lead = LeadOut(**_base_kwargs(raw_copper_data={"some_other_field": "x"}))

    assert lead.applied_at == lead.created_at


def test_applied_at_falls_back_to_created_at_when_date_created_is_malformed():
    lead = LeadOut(**_base_kwargs(raw_copper_data={"date_created": "not-a-timestamp"}))

    assert lead.applied_at == lead.created_at


def test_applied_at_falls_back_to_created_at_when_date_created_is_none():
    lead = LeadOut(**_base_kwargs(raw_copper_data={"date_created": None}))

    assert lead.applied_at == lead.created_at


def test_applied_at_excluded_from_serialized_output():
    lead = LeadOut(**_base_kwargs(raw_copper_data={"date_created": 1750000000}))

    dumped = lead.model_dump()
    assert "raw_copper_data" not in dumped
    assert dumped["applied_at"] == lead.applied_at


def test_applied_at_column_value_is_used_as_is_when_present():
    """Issue #217: once `applied_at` is a real column, a non-null value from
    the DB row must win outright -- no re-derivation from raw_copper_data,
    even if raw_copper_data would compute something different."""
    column_value = datetime(2026, 3, 1, tzinfo=timezone.utc)
    lead = LeadOut(**_base_kwargs(applied_at=column_value, raw_copper_data={"date_created": 1750000000}))

    assert lead.applied_at == column_value


def test_applied_at_column_null_still_falls_back_to_raw_copper_data():
    """Pre-#217 rows (or any row the backfill couldn't resolve) must keep
    serializing exactly like before the column existed."""
    epoch_seconds = 1750000000
    lead = LeadOut(**_base_kwargs(applied_at=None, raw_copper_data={"date_created": epoch_seconds}))

    assert lead.applied_at == datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
