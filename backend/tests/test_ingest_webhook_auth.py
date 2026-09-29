"""
Copper webhook auth on POST /api/v1/leads/ingest.

Copper cannot HMAC-sign notifications; it only replays the static `headers`
registered on the subscription. These tests pin that the X-Copper-Webhook-Token
header (compared to COPPER_WEBHOOK_SECRET) gets a request past the gate, that
X-Copper-Signature HMAC still works, and that everything else fails closed
with 401 before the body is parsed or the DB is touched.
"""
from __future__ import annotations
import hashlib
import hmac

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.auth import verify_webhook_signature

client = TestClient(app)
SECRET = "a" * 64


def test_matching_token_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    assert verify_webhook_signature(b"{}", "", SECRET) is True


def test_wrong_or_missing_token_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    assert verify_webhook_signature(b"{}", "", "b" * 64) is False
    assert verify_webhook_signature(b"{}", "", "") is False


def test_unset_secret_rejects_even_an_empty_token(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", "")
    assert verify_webhook_signature(b"{}", "", "") is False


def test_hmac_signature_still_accepted(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    sig = hmac.new(SECRET.encode(), b"{}", hashlib.sha256).hexdigest()
    assert verify_webhook_signature(b"{}", sig, "") is True


def test_ingest_401s_on_a_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    r = client.post("/api/v1/leads/ingest", content=b"{}",
                    headers={"Content-Type": "application/json", "X-Copper-Webhook-Token": "nope"})
    assert r.status_code == 401


def test_ingest_401s_with_no_credentials(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    r = client.post("/api/v1/leads/ingest", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_ingest_lets_a_valid_token_past_the_gate(monkeypatch):
    """A body that isn't JSON gets 400 only after verification passed -- proves
    the token opened the gate without needing a DB."""
    monkeypatch.setattr(settings, "copper_webhook_secret", SECRET)
    r = client.post("/api/v1/leads/ingest", content=b"not json",
                    headers={"Content-Type": "application/json", "X-Copper-Webhook-Token": SECRET})
    assert r.status_code == 400
