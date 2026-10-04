"""
Unit tests for app.services.bulk_rejection.eligibility_reason (issue #205):
the shared exclude-never-fix gate used by the bulk-send-rejection preview,
the send endpoint's re-check, and the per-lead Celery task's own re-check.
"""
from __future__ import annotations
import uuid
from types import SimpleNamespace

from app.services import bulk_rejection


def _lead(
    *,
    status="assessed",
    copper_opportunity_id=None,
    recipient_email="founder@acme.test",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        company_name="Acme Deep Tech",
        status=status,
        copper_opportunity_id=copper_opportunity_id,
        raw_copper_data={"recipient_email": recipient_email} if recipient_email else {},
    )


def _card(
    *,
    bucket="REJECT",
    user_override=None,
    sent_at=None,
    draft_type="rejection",
    draft_body="Thanks for applying.",
    draft_bucket="REJECT",
):
    return SimpleNamespace(
        bucket=bucket,
        user_override=user_override,
        sent_at=sent_at,
        draft_type=draft_type,
        draft_body=draft_body,
        draft_bucket=draft_bucket,
    )


def test_eligible_lead_has_no_reason():
    assert bulk_rejection.eligibility_reason(_lead(), _card()) is None


def test_no_assessment_card_is_ineligible():
    assert bulk_rejection.eligibility_reason(_lead(), None) == bulk_rejection.REASON_NO_ASSESSMENT


def test_bucket_not_reject_is_ineligible():
    assert bulk_rejection.eligibility_reason(_lead(), _card(bucket="YES")) == bulk_rejection.REASON_NOT_REJECT_BUCKET


def test_user_override_to_non_reject_is_ineligible():
    """Effective bucket (user_override or bucket) governs, not the AI's
    original bucket."""
    card = _card(bucket="REJECT", user_override="MAYBE")
    assert bulk_rejection.eligibility_reason(_lead(), card) == bulk_rejection.REASON_NOT_REJECT_BUCKET


def test_already_sent_is_ineligible():
    import datetime
    card = _card(sent_at=datetime.datetime(2026, 1, 1))
    assert bulk_rejection.eligibility_reason(_lead(), card) == bulk_rejection.REASON_ALREADY_SENT


def test_no_recipient_email_is_ineligible():
    lead = _lead(recipient_email=None)
    assert bulk_rejection.eligibility_reason(lead, _card()) == bulk_rejection.REASON_NO_RECIPIENT


def test_no_draft_body_is_ineligible():
    card = _card(draft_body=None)
    assert bulk_rejection.eligibility_reason(_lead(), card) == bulk_rejection.REASON_STALE_OR_MISSING_DRAFT


def test_wrong_draft_type_is_ineligible():
    card = _card(draft_type="meeting_request")
    assert bulk_rejection.eligibility_reason(_lead(), card) == bulk_rejection.REASON_STALE_OR_MISSING_DRAFT


def test_stale_draft_bucket_mismatch_is_ineligible():
    """Issue #150's stale-draft guard, exercised in bulk: a lead currently in
    REJECT but whose draft was written for a bucket it used to be (draft_bucket
    disagrees with the effective bucket) must never be sent in bulk."""
    card = _card(bucket="REJECT", draft_bucket="YES")
    assert bulk_rejection.eligibility_reason(_lead(), card) == bulk_rejection.REASON_STALE_OR_MISSING_DRAFT


def test_archived_lead_is_ineligible():
    lead = _lead(status="archived")
    assert bulk_rejection.eligibility_reason(lead, _card()) == bulk_rejection.REASON_ARCHIVED_OR_CONVERTED


def test_converted_lead_is_ineligible():
    lead = _lead(copper_opportunity_id="opp-1")
    assert bulk_rejection.eligibility_reason(lead, _card()) == bulk_rejection.REASON_ARCHIVED_OR_CONVERTED


def test_recipient_email_reads_raw_copper_data():
    lead = _lead(recipient_email="founder@acme.test")
    assert bulk_rejection.recipient_email(lead) == "founder@acme.test"


def test_recipient_email_none_when_raw_copper_data_missing():
    lead = SimpleNamespace(raw_copper_data=None)
    assert bulk_rejection.recipient_email(lead) is None
