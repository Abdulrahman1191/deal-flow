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
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import register_copper_webhooks as rcw  # noqa: E402

URL = "https://deal-flow.apps.raed.vc/api/v1/leads/ingest"


def _sub(id_, target, event, url=URL):
    return {"id": id_, "target": target, "event": event, "url": url}


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
