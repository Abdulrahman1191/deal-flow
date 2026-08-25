"""
Tests for learned one-click reason chips (issue #152):
  - app/services/learned_reasons.py: pure clustering/ranking logic
  - GET /overrides/my-reasons: the endpoint that serves them, scoped per user

Endpoint tests use the same fake-session pattern as
test_overrides_calibration.py -- no live Postgres needed.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.learned_reasons import build_learned_reasons

client = TestClient(app)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _row(trigger, human_bucket=None, reason=None, tags=None, days_ago=0):
    return SimpleNamespace(
        trigger=trigger,
        human_bucket=human_bucket,
        human_reason=reason,
        human_reason_tags=tags,
        created_at=NOW - timedelta(days=days_ago),
    )


# ---------- pure logic: build_learned_reasons ----------


def test_near_duplicate_phrasings_collapse_to_one_chip():
    rows = [
        _row("override", "REJECT", reason="not deep tech", days_ago=10),
        _row("override", "REJECT", reason="Not deep tech enough", days_ago=5),
        _row("override", "REJECT", reason="isn't deep tech", days_ago=1),
    ]
    result = build_learned_reasons(rows, now=NOW)
    assert len(result["bucket_reject"]) == 1
    chip = result["bucket_reject"][0]
    assert chip.count == 3
    assert "deep tech" in chip.text.lower()


def test_unrelated_reasons_stay_separate():
    rows = [
        _row("rate_down", reason="Weak founder-market fit"),
        _row("rate_down", reason="Market too small"),
    ]
    result = build_learned_reasons(rows, now=NOW)
    assert len(result["rating_down"]) == 2


def test_other_tag_alone_is_not_a_candidate():
    rows = [_row("confirm", tags=["Other"])]
    result = build_learned_reasons(rows, now=NOW)
    assert result.get("rating_up", []) == []


def test_tags_and_free_text_both_become_candidates():
    rows = [
        _row("confirm", tags=["Strong founder-market fit"]),
        _row("confirm", reason="Strong founder-market fit"),
    ]
    result = build_learned_reasons(rows, now=NOW)
    assert len(result["rating_up"]) == 1
    assert result["rating_up"][0].count == 2


def test_approve_and_skip_triggers_are_ignored():
    """approve/skip are auto-captures with no reason UI today -- rows with
    those triggers must never contribute chips even if a reason is somehow
    present, since context_for() has no mapping for them."""
    rows = [_row("approve", reason="whatever"), _row("skip", reason="whatever")]
    result = build_learned_reasons(rows, now=NOW)
    assert result == {}


def test_frequency_beats_recency_but_recency_breaks_ties():
    rows = [
        _row("rate_down", reason="Overhyped", days_ago=1),
        _row("rate_down", reason="Weak team", days_ago=1),
        _row("rate_down", reason="Weak team", days_ago=2),
        _row("rate_down", reason="Weak team", days_ago=3),
    ]
    result = build_learned_reasons(rows, now=NOW)
    texts = [c.text for c in result["rating_down"]]
    assert texts[0] == "Weak team"  # count=3 beats count=1


def test_cap_limits_chips_per_context():
    # Deliberately unrelated phrasings -- reasons that only differ by a
    # trailing counter (e.g. "reason 0" / "reason 1") are similar enough to
    # collapse into each other under the same dedupe pass this is testing,
    # which would make the cluster count (not the cap) the limiting factor.
    distinct_reasons = [
        "Weak team", "Bad market timing", "No real moat", "Wrong stage",
        "Off-thesis", "Copycat model", "Regulatory risk", "Cap table issues",
        "No traction yet", "Unclear ownership structure", "Too early for us",
        "Bad unit economics",
    ]
    rows = [_row("rate_down", reason=r) for r in distinct_reasons]
    result = build_learned_reasons(rows, cap=8, now=NOW)
    assert len(result["rating_down"]) == 8


# ---------- endpoint: GET /overrides/my-reasons ----------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.queries = []

    async def execute(self, query, params=None):
        self.queries.append(query)
        return _FakeResult(self._results.pop(0))


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results) -> _RecordingSession:
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _many(row, n):
    return [row] * n


def test_my_reasons_groups_by_context_and_dedupes():
    personal_rows = [
        *_many(_row("override", "REJECT", reason="not deep tech"), 3),
        *_many(_row("override", "REJECT", reason="Not deep tech enough"), 3),
        *_many(_row("confirm", reason="Strong team"), 3),
    ]
    _auth_as("reviewer@raed.vc")
    # Every context here has < 3 distinct chips, so the sparse-context team
    # fallback also queries -- queue an empty team result for it.
    _use_db([personal_rows, []])
    try:
        response = client.get("/api/v1/overrides/my-reasons")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["bucket_reject"]) == 1
    assert body["bucket_reject"][0]["count"] == 6
    assert body["bucket_reject"][0]["source"] == "personal"
    assert len(body["rating_up"]) == 1
    assert body["rating_down"] == []
    assert body["bucket_yes"] == []
    assert body["bucket_maybe"] == []


def test_my_reasons_query_is_scoped_to_the_caller_email():
    """Personal reasons must only ever be pulled via a WHERE clause bound to
    the caller's own email -- this is what keeps another user's reasons out
    of the response."""
    _auth_as("reviewer@raed.vc")
    session = _use_db([[], []])
    try:
        response = client.get("/api/v1/overrides/my-reasons")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    # Personal result is empty in every context -> team fallback query also runs.
    assert len(session.queries) == 2
    personal_sql = str(session.queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "acted_by_email = 'reviewer@raed.vc'" in personal_sql

    team_sql = str(session.queries[1].compile(compile_kwargs={"literal_binds": True}))
    assert "acted_by_email IS NOT NULL" in team_sql
    # Exclusion operator rendering is dialect-dependent ("!=" vs "<>") --
    # accept either rather than pinning to one.
    assert (
        "acted_by_email != 'reviewer@raed.vc'" in team_sql
        or "acted_by_email <> 'reviewer@raed.vc'" in team_sql
    )


def test_team_fallback_does_not_leak_into_a_context_with_enough_personal_reasons():
    personal_rows = [
        _row("confirm", reason="Strong team"),
        _row("confirm", reason="Great tech moat"),
        _row("confirm", reason="Big market"),
    ]
    team_rows = [_row("confirm", reason="Some other partner's reason")]
    _auth_as("reviewer@raed.vc")
    # rating_up alone has >= 3 distinct chips, but every OTHER context is
    # still sparse (no personal rows at all), so the team query still runs --
    # it must not leak into rating_up though.
    _use_db([personal_rows, team_rows])
    try:
        response = client.get("/api/v1/overrides/my-reasons")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["rating_up"]) == 3
    assert all(c["source"] == "personal" for c in body["rating_up"])


def test_my_reasons_team_fallback_fills_sparse_context_without_duplicating_personal():
    personal_rows = [_row("rate_down", reason="Overhyped")]
    team_rows = [
        *_many(_row("rate_down", reason="Overhyped"), 5),  # dup of personal -> excluded
        *_many(_row("rate_down", reason="No real moat"), 4),
    ]
    _auth_as("reviewer@raed.vc")
    _use_db([personal_rows, team_rows])
    try:
        response = client.get("/api/v1/overrides/my-reasons")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    chips = body["rating_down"]
    assert len(chips) == 2
    by_source = {c["source"] for c in chips}
    assert by_source == {"personal", "team"}
    texts = {c["text"].lower() for c in chips}
    assert "overhyped" in texts
    assert "no real moat" in texts
