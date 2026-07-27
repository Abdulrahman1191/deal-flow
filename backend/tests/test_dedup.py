"""
Tests for app/services/dedup.py (issue #73).

dedupe_leads() groups active leads by (owner_email, normalized company_name)
-- never across owners -- and archives every loser in a group after copying
over any deck/assessment the winning canonical lead is missing. Exercised
with a fake AsyncSession (mirrors the pattern in test_multiuser_access.py's
_RecordingSession and test_backfill_awaiting_deck.py's _FakeSession): no live
Postgres needed, so this stays green in CI.

The pure helpers (normalize_name, _has_deck, _inherit_deck,
_inherit_assessment) are also covered directly, without going through the DB
at all.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import dedup


def _lead(owner_email, company_name="Acme", copper_id=None, created_at=None,
          pitch_deck_drive_id=None, pitch_deck_filename=None, pitch_deck_text=None,
          pitch_deck_ingested_at=None, pitch_deck_s3=None, status="pending", lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=owner_email,
        company_name=company_name,
        copper_id=copper_id,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        pitch_deck_drive_id=pitch_deck_drive_id,
        pitch_deck_filename=pitch_deck_filename,
        pitch_deck_text=pitch_deck_text,
        pitch_deck_ingested_at=pitch_deck_ingested_at,
        pitch_deck_s3=pitch_deck_s3,
        status=status,
    )


def _card(lead_id, user_override=None, user_rating=None, research_data=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        user_override=user_override,
        user_rating=user_rating,
        research_data=research_data,
    )


# --- pure helpers --------------------------------------------------------------


def test_normalize_name_collapses_whitespace_and_case():
    assert dedup.normalize_name("  Acme   Deep  Tech ") == "acme deep tech"
    assert dedup.normalize_name(None) == ""


def test_has_deck_true_for_drive_id_or_text():
    assert dedup._has_deck(_lead("a", pitch_deck_drive_id="drv1")) is True
    assert dedup._has_deck(_lead("a", pitch_deck_text="some extracted text")) is True
    assert dedup._has_deck(_lead("a")) is False


def test_inherit_deck_copies_fields_from_best_loser_and_skips_if_canonical_has_one():
    canonical = _lead("a")
    loser_no_deck = _lead("a")
    loser_with_deck = _lead(
        "a", pitch_deck_drive_id="drv1", pitch_deck_filename="Acme.pdf",
        pitch_deck_text="extracted text", pitch_deck_ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    inherited = dedup._inherit_deck(canonical, [loser_no_deck, loser_with_deck])

    assert inherited is True
    assert canonical.pitch_deck_drive_id == "drv1"
    assert canonical.pitch_deck_filename == "Acme.pdf"
    assert canonical.pitch_deck_text == "extracted text"

    # canonical already has a deck -- no-op, even if a loser also has one
    already_has_deck = _lead("a", pitch_deck_drive_id="existing")
    assert dedup._inherit_deck(already_has_deck, [loser_with_deck]) is False
    assert already_has_deck.pitch_deck_drive_id == "existing"


def test_inherit_assessment_reassigns_lead_id_only_when_canonical_lacks_a_card():
    canonical = _lead("a")
    loser = _lead("a")
    loser_card = _card(loser.id)

    inherited = dedup._inherit_assessment(canonical, None, [(loser, loser_card)])
    assert inherited is True
    assert loser_card.lead_id == canonical.id

    # canonical already has a card -- must not steal the loser's
    existing_card = _card(canonical.id)
    inherited_again = dedup._inherit_assessment(canonical, existing_card, [(loser, loser_card)])
    assert inherited_again is False


# --- dedupe_leads(): fake AsyncSession -----------------------------------------


class _ScalarsResult:
    """Backs `(await db.execute(select(Lead)...)).scalars().all()`."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _ScalarOneResult:
    """Backs `(await db.execute(select(AssessmentCard)...)).scalar_one_or_none()`."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Routes execute() by query target entity: the leads list for
    `select(Lead)`, and a per-lead-id lookup for `select(AssessmentCard)...`
    (matched via the compiled bind params, mirroring _RecordingSession in
    test_multiuser_access.py -- no live DB needed)."""

    def __init__(self, leads, cards_by_lead_id):
        self._leads = list(leads)
        self._cards_by_lead_id = cards_by_lead_id
        self.added: list = []
        self.committed = 0

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _ScalarsResult(self._leads)
        assert entity is AssessmentCard
        params = dict(query.compile().params).values()
        lead_id = next(v for v in params if v in self._cards_by_lead_id)
        return _ScalarOneResult(self._cards_by_lead_id[lead_id])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _run(leads, cards_by_lead_id=None, commit=False):
    cards_by_lead_id = cards_by_lead_id or {l.id: None for l in leads}
    session = _FakeSession(leads, cards_by_lead_id)
    report = asyncio.run(dedup.dedupe_leads(session, commit=commit))
    return session, report


def test_same_owner_same_name_collapses_to_one_canonical():
    older_with_copper = _lead("alice@raed.vc", "Acme Co", copper_id="C1",
                               created_at=datetime(2025, 6, 1, tzinfo=timezone.utc))
    newer_no_copper = _lead("alice@raed.vc", "Acme Co", copper_id=None,
                             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    leads = [older_with_copper, newer_no_copper]

    session, report = _run(leads, commit=True)

    assert report["groups"] == 1
    assert report["to_archive"] == 1
    assert report["archived"] == 1
    assert older_with_copper.status == "pending"  # has_copper wins the tie -- kept canonical
    assert newer_no_copper.status == "archived"
    assert session.committed == 1
    assert len(session.added) == 1  # one lead_event logged for the archived loser


def test_dry_run_makes_no_writes():
    lead1 = _lead("alice@raed.vc", "Acme Co", copper_id="C1")
    lead2 = _lead("alice@raed.vc", "Acme Co", copper_id=None)

    session, report = _run([lead1, lead2], commit=False)

    assert report["to_archive"] == 1
    assert lead1.status == "pending"
    assert lead2.status == "pending"
    assert session.committed == 0
    assert session.added == []


def test_canonical_inherits_deck_from_non_canonical_loser():
    # canonical wins on `assessed` (has a card) despite having no deck
    canonical = _lead("alice@raed.vc", "Acme Co", copper_id="C1")
    canonical_card = _card(canonical.id)
    loser_with_deck = _lead(
        "alice@raed.vc", "Acme Co", copper_id="C2",
        pitch_deck_drive_id="drv1", pitch_deck_filename="Acme.pdf",
        pitch_deck_text="extracted deck text",
    )

    cards = {canonical.id: canonical_card, loser_with_deck.id: None}
    session, report = _run([canonical, loser_with_deck], cards_by_lead_id=cards, commit=True)

    assert report["detail"][0]["deck_inherited"] is True
    assert canonical.pitch_deck_drive_id == "drv1"
    assert canonical.pitch_deck_filename == "Acme.pdf"
    assert canonical.pitch_deck_text == "extracted deck text"
    assert canonical.status == "pending"
    assert loser_with_deck.status == "archived"


def test_different_owner_same_name_leads_are_untouched():
    alice_lead = _lead("alice@raed.vc", "Acme Co", copper_id="C1")
    bob_lead = _lead("bob@raed.vc", "Acme Co", copper_id="C2")

    session, report = _run([alice_lead, bob_lead], commit=True)

    assert report["groups"] == 0
    assert report["to_archive"] == 0
    assert report["archived"] == 0
    assert alice_lead.status == "pending"
    assert bob_lead.status == "pending"
    assert session.committed == 1
    assert session.added == []


def test_idempotent_second_run_with_nothing_to_merge_does_nothing():
    lead1 = _lead("alice@raed.vc", "Acme Co", copper_id="C1")
    lead2 = _lead("alice@raed.vc", "Acme Co", copper_id=None, status="archived")

    # second run only ever sees non-archived leads (mirrors the `status != "archived"`
    # filter in the real query) -- the fake session's leads list stands in for that.
    session, report = _run([lead1], commit=True)

    assert report["groups"] == 0
    assert report["archived"] == 0
    assert lead1.status == "pending"
    assert lead2.status == "archived"  # untouched, already archived from a prior run
