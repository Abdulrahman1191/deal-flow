"""
Tests for issue #90's Copper activity-feed reading: fetching a lead's (and
its linked person's) activities via POST /activities/search, filtering to
Email activities, and computing whether any of them predate the lead's
application (genuine prior contact) vs. only our own post-application
automated outreach.

httpx is always mocked here -- mirrors the _FakeHttpClient pattern in
test_copper_writebacks.py. No live Copper calls, no live DB.
"""
from app.services import copper_service as cs


class _FakeHttpResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class _FakeActivitiesClient:
    """Mocks httpx.Client for fetch_activities_for_parent. `pages_by_parent_type`
    maps parent type ("lead"/"person") to a list of pages (each page a list
    of raw activity dicts) -- page_number indexes into it, 1-based."""

    def __init__(self, pages_by_parent_type, calls):
        self._pages_by_parent_type = pages_by_parent_type
        self._calls = calls

    def __call__(self, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append(json)
        pages = self._pages_by_parent_type[json["parent"]["type"]]
        page_number = json["page_number"]
        batch = pages[page_number - 1] if page_number <= len(pages) else []
        return _FakeHttpResponse(batch)


def _email_activity(activity_date, category="user", type_id=637593):
    return {"type": {"category": category, "id": type_id}, "activity_date": activity_date}


def _form_submitted_activity(activity_date):
    return {"type": {"category": "system", "id": 48}, "activity_date": activity_date}


def _other_activity(activity_date):
    return {"type": {"category": "system", "id": 1}, "activity_date": activity_date}


# --- fetch_lead_activities: parent shape + pagination + person merge -------

def test_fetch_lead_activities_merges_lead_and_person_and_paginates(monkeypatch):
    calls = []
    fake_client = _FakeActivitiesClient(
        {
            "lead": [[{"id": i} for i in range(cs.PAGE_SIZE)], [{"id": "last-lead"}]],
            "person": [[{"id": "person-1"}]],
        },
        calls,
    )
    monkeypatch.setattr(cs.httpx, "Client", fake_client)

    activities = cs.fetch_lead_activities("111", copper_person_id="222")

    assert len(activities) == cs.PAGE_SIZE + 1 + 1
    assert {"type": "lead", "id": 111} in [c["parent"] for c in calls]
    assert {"type": "person", "id": 222} in [c["parent"] for c in calls]
    lead_calls = [c for c in calls if c["parent"]["type"] == "lead"]
    assert len(lead_calls) == 2  # first page was full -> a second page was fetched


def test_fetch_lead_activities_skips_person_when_not_set(monkeypatch):
    calls = []
    fake_client = _FakeActivitiesClient({"lead": [[]]}, calls)
    monkeypatch.setattr(cs.httpx, "Client", fake_client)

    activities = cs.fetch_lead_activities("111")

    assert activities == []
    assert all(c["parent"]["type"] == "lead" for c in calls)


# --- filter_email_activities: both confirmed-live Email type ids -----------

def test_filter_email_activities_matches_both_user_and_system_email_types():
    activities = [
        _email_activity(100, category="user", type_id=637593),
        _email_activity(200, category="system", type_id=6),
        _other_activity(300),
    ]
    filtered = cs.filter_email_activities(activities)
    assert len(filtered) == 2


def test_filter_email_activities_excludes_non_email_types():
    activities = [_other_activity(100), _form_submitted_activity(200)]
    assert cs.filter_email_activities(activities) == []


# --- resolve_application_epoch: Form Submitted preferred over date_created --

def test_resolve_application_epoch_prefers_form_submitted_over_date_created():
    activities = [_form_submitted_activity(500)]
    assert cs.resolve_application_epoch(activities, lead_date_created=1000) == 500


def test_resolve_application_epoch_falls_back_to_date_created():
    assert cs.resolve_application_epoch([], lead_date_created=1000) == 1000


def test_resolve_application_epoch_none_when_neither_available():
    assert cs.resolve_application_epoch([], lead_date_created=None) is None


# --- compute_prior_contact: the core prior-vs-automated heuristic ----------

def test_compute_prior_contact_true_for_pre_application_email():
    """Acceptance criterion: a lead with a pre-application Email activity ->
    prior_contact=true with count/date."""
    application_date = 1_700_000_000
    activities = [
        _form_submitted_activity(application_date),
        _email_activity(application_date - 86400),  # a day before -> genuine prior contact
        _email_activity(application_date + 3600),  # our own outreach, after -> excluded
    ]
    result = cs.compute_prior_contact(activities, lead_date_created=application_date)

    assert result["prior_contact"] is True
    assert result["prior_contact_count"] == 1
    assert result["prior_contact_last_at"].timestamp() == application_date - 86400


def test_compute_prior_contact_false_for_post_application_automated_outreach_only():
    """Acceptance criterion: a lead with only post-application automated
    outreach -> prior_contact=false."""
    application_date = 1_700_000_000
    activities = [
        _form_submitted_activity(application_date),
        _email_activity(application_date + 60),
        _email_activity(application_date + 3600),
    ]
    result = cs.compute_prior_contact(activities, lead_date_created=application_date)

    assert result["prior_contact"] is False
    assert result["prior_contact_count"] == 0
    assert result["prior_contact_last_at"] is None


def test_compute_prior_contact_counts_multiple_pre_application_emails_and_picks_latest():
    application_date = 1_700_000_000
    activities = [
        _email_activity(application_date - 100_000),
        _email_activity(application_date - 500),
        _email_activity(application_date + 100),  # after -> excluded
    ]
    result = cs.compute_prior_contact(activities, lead_date_created=application_date)

    assert result["prior_contact"] is True
    assert result["prior_contact_count"] == 2
    assert result["prior_contact_last_at"].timestamp() == application_date - 500


def test_compute_prior_contact_ignores_non_email_activities_before_application():
    application_date = 1_700_000_000
    activities = [_other_activity(application_date - 1000)]
    result = cs.compute_prior_contact(activities, lead_date_created=application_date)

    assert result["prior_contact"] is False


def test_compute_prior_contact_no_application_date_defaults_to_false():
    result = cs.compute_prior_contact([_email_activity(100)], lead_date_created=None)

    assert result == {
        "prior_contact": False,
        "prior_contact_count": 0,
        "prior_contact_last_at": None,
    }
