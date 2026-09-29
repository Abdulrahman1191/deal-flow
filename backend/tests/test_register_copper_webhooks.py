"""
Tests for scripts/register_copper_webhooks.py (issue #171).

Copper has zero webhook subscriptions registered in production, which is why
POST /api/v1/leads/ingest never fires there today. This script is the
human-run, repeatable way to fix that -- these tests pin its two safety
properties: it never writes to Copper without --commit, and a second --commit
run against an already-registered account creates nothing (no duplicate
subscriptions for the same target/event/url).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import httpx  # noqa: E402

import register_copper_webhooks as rcw  # noqa: E402

URL = "https://deal-flow.apps.raed.vc/api/v1/leads/ingest"


def _sub(id_, entity_type, event, url=URL):
    """A subscription as Copper's GET /webhooks returns it: `type` is the
    entity, `target` is the URL."""
    return {"id": id_, "type": entity_type, "event": event, "target": url}


# --- pure planning logic -------------------------------------------------------

def test_plan_registrations_with_no_existing_subscriptions_wants_all_three():
    missing = rcw.plan_registrations([], URL)
    assert set(missing) == {("lead", "new"), ("lead", "update"), ("lead", "delete")}


def test_plan_registrations_skips_pairs_already_registered_for_this_url():
    existing = [_sub(1, "lead", "new"), _sub(2, "lead", "update")]
    missing = rcw.plan_registrations(existing, URL)
    assert missing == [("lead", "delete")]


def test_plan_registrations_is_empty_once_all_three_exist():
    existing = [_sub(1, "lead", "new"), _sub(2, "lead", "update"), _sub(3, "lead", "delete")]
    assert rcw.plan_registrations(existing, URL) == []


def test_plan_registrations_ignores_a_subscription_for_a_different_url():
    """A subscription pointed at a stale/different URL (e.g. pre-migration)
    must not mask the fact that the current URL isn't covered."""
    existing = [_sub(1, "lead", "new", url="https://old-host.example.com/ingest")]
    missing = rcw.plan_registrations(existing, URL)
    assert ("lead", "new") in missing


def test_plan_registrations_ignores_subscriptions_for_other_targets():
    existing = [_sub(1, "person", "new", url=URL)]
    missing = rcw.plan_registrations(existing, URL)
    assert set(missing) == {("lead", "new"), ("lead", "update"), ("lead", "delete")}


# --- CLI: dry-run by default, idempotent with --commit -------------------------

def test_main_never_creates_without_commit(monkeypatch, capsys):
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "shh")
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: [])
    created = []
    monkeypatch.setattr(rcw, "create_subscription", lambda *a, **k: created.append(a) or {"id": 1})

    exit_code = rcw.main(["--url", URL])

    assert exit_code == 0
    assert created == []
    assert "Dry run" in capsys.readouterr().out


def test_main_with_commit_creates_only_missing_subscriptions(monkeypatch):
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "shh")
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: [_sub(1, "lead", "new")])
    created = []
    monkeypatch.setattr(
        rcw, "create_subscription",
        lambda target, event, url, secret: created.append((target, event, url, secret)) or {"id": 99},
    )

    exit_code = rcw.main(["--url", URL, "--commit"])

    assert exit_code == 0
    assert set((t, e) for t, e, _, _ in created) == {("lead", "update"), ("lead", "delete")}
    assert all(url == URL and secret == "shh" for _, _, url, secret in created)


def test_second_commit_run_is_a_no_op_once_everything_is_registered(monkeypatch):
    """Idempotency: rerunning --commit against an account that already has
    all three subscriptions must create nothing."""
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "shh")
    monkeypatch.setattr(
        rcw, "list_subscriptions",
        lambda: [_sub(1, "lead", "new"), _sub(2, "lead", "update"), _sub(3, "lead", "delete")],
    )
    created = []
    monkeypatch.setattr(rcw, "create_subscription", lambda *a, **k: created.append(a) or {"id": 1})

    exit_code = rcw.main(["--url", URL, "--commit"])

    assert exit_code == 0
    assert created == []


def test_main_refuses_without_a_configured_signing_secret(monkeypatch):
    """Registering a webhook Copper would sign with a secret we can't verify
    against would just recreate today's problem with extra steps."""
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "")
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: (_ for _ in ()).throw(
        AssertionError("must not call Copper when the secret is unset")
    ))

    exit_code = rcw.main(["--url", URL, "--commit"])

    assert exit_code == 1


def test_main_refuses_without_a_configured_target_url(monkeypatch, capsys):
    """No --url and no COPPER_WEBHOOK_TARGET_URL configured must refuse
    rather than register a subscription pointed at nothing (issue #174)."""
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "shh")
    monkeypatch.setattr(rcw.settings, "copper_webhook_target_url", "")
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: (_ for _ in ()).throw(
        AssertionError("must not call Copper when the target URL is unset")
    ))

    exit_code = rcw.main(["--commit"])

    assert exit_code == 1
    assert "COPPER_WEBHOOK_TARGET_URL" in capsys.readouterr().out


def test_main_falls_back_to_configured_target_url_when_no_flag_given(monkeypatch, capsys):
    """With no --url, the script uses COPPER_WEBHOOK_TARGET_URL rather than
    a hardcoded default."""
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "shh")
    monkeypatch.setattr(rcw.settings, "copper_webhook_target_url", URL)
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: [])
    created = []
    monkeypatch.setattr(rcw, "create_subscription", lambda *a, **k: created.append(a) or {"id": 1})

    exit_code = rcw.main([])

    assert exit_code == 0
    assert "Dry run" in capsys.readouterr().out


# --- --list --------------------------------------------------------------------

def test_list_flag_prints_and_exits_without_planning_or_writes(monkeypatch, capsys):
    monkeypatch.setattr(rcw.settings, "copper_webhook_secret", "")
    monkeypatch.setattr(rcw.settings, "copper_webhook_target_url", "")
    monkeypatch.setattr(rcw, "list_subscriptions", lambda: [_sub(1, "lead", "new")])
    monkeypatch.setattr(rcw, "create_subscription", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("--list must never write")
    ))

    exit_code = rcw.main(["--list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "id=1" in out
    assert "type=lead" in out
    assert f"target={URL}" in out


# --- create payload: Copper's field names ---------------------------------------

# Copper's verbatim response to the payload this script used to send
# ({"target": "lead", "url": ..., "secret": {...}}) on 2026-09-29.
RECORDED_422 = {
    "success": False,
    "status": 422,
    "message": "Invalid input: Validation errors: Base: Unrecognized attributes specified: url\nType: can't be blank",
}


def _fake_copper(sent):
    """A stand-in for POST /developer_api/v1/webhooks that validates the way
    Copper did when it returned RECORDED_422."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "url" in body or not body.get("type"):
            return httpx.Response(422, json=RECORDED_422)
        return httpx.Response(200, json={"id": 4242, **body})
    real_client = httpx.Client
    return lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)


def test_create_subscription_is_accepted_by_copper_validation(monkeypatch):
    sent = []
    monkeypatch.setattr(rcw.httpx, "Client", _fake_copper(sent))

    created = rcw.create_subscription("lead", "delete", URL, "shh")

    assert created["id"] == 4242
    assert sent == [{
        "type": "lead",
        "event": "delete",
        "target": URL,
        "headers": {"X-Copper-Webhook-Token": "shh"},
    }]


def test_the_old_payload_shape_reproduces_the_recorded_422(monkeypatch):
    """Guards the fake itself: the pre-fix body must still fail the way Copper
    failed, so the test above can't pass against a lenient stand-in."""
    sent = []
    monkeypatch.setattr(rcw.httpx, "Client", _fake_copper(sent))
    old_body = {"target": "lead", "event": "new", "url": URL, "secret": {"secret": "shh"}}

    with rcw.httpx.Client(timeout=30) as client:
        response = client.post(f"{rcw.COPPER_BASE}/webhooks", json=old_body)

    assert response.status_code == 422
    assert response.json() == RECORDED_422


def test_secret_goes_in_headers_never_in_the_echoed_secret_field():
    """Copper echoes a subscription's `secret` values inside every notification
    body; the shared secret must travel only as the replayed header."""
    body = rcw.subscription_body("lead", "new", URL, "shh")
    assert "secret" not in body
    assert body["headers"] == {"X-Copper-Webhook-Token": "shh"}
