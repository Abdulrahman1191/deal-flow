"""
Register Copper webhook subscriptions for the `lead` resource (issue #171).

Copper currently has ZERO webhook subscriptions registered (GET
/developer_api/v1/webhooks -> []), so POST /api/v1/leads/ingest never fires in
production -- every lead change (new/update/delete/reassignment) only reaches
the board via the polling tasks (sync_copper_leads_task every 5 min,
reconcile_ownership_task every ownership_reconcile_interval_seconds). This
script makes registering the missing subscriptions repeatable and safe: it
lists what's already registered, and with --commit creates exactly the
`lead` new/update/delete subscriptions that are missing, pointed at the
configured ingest URL with the shared signing secret
(app.services.auth.verify_webhook_signature validates against the same
COPPER_WEBHOOK_SECRET).

Dry-run by default: with no flags, it only prints what it would create.
Idempotent: never creates a duplicate for a (target, event) pair that
already targets the same URL -- rerunning after a partial/failed run, or
after someone has already registered some of the three events by hand,
only creates what's still missing.

This is a human-run script (it writes to Copper) -- registering the
webhooks themselves is out of scope for the issue this closes.

Usage (from backend/):
  python scripts/register_copper_webhooks.py
  python scripts/register_copper_webhooks.py --url https://deal-flow.apps.raed.vc/api/v1/leads/ingest --commit

Reads COPPER_API_KEY, COPPER_USER_EMAIL, COPPER_WEBHOOK_SECRET from the
environment (same as the app).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.services.copper_service import COPPER_BASE, _headers

TARGET = "lead"
EVENTS = ("new", "update", "delete")

# The board's production ingest endpoint (see CLAUDE.md). Overridable via
# --url for local/staging testing against a tunnel URL.
DEFAULT_INGEST_URL = "https://deal-flow.apps.raed.vc/api/v1/leads/ingest"


# --- Copper I/O ---------------------------------------------------------------

def list_subscriptions() -> list[dict]:
    """GET /developer_api/v1/webhooks -- every subscription on the account,
    regardless of target/event."""
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{COPPER_BASE}/webhooks", headers=_headers())
        response.raise_for_status()
        return response.json() or []


def create_subscription(target: str, event: str, url: str, secret: str) -> dict:
    """POST /developer_api/v1/webhooks -- registers one (target, event) pair."""
    body = {"target": target, "event": event, "url": url, "secret": {"secret": secret}}
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{COPPER_BASE}/webhooks", headers=_headers(), json=body)
        response.raise_for_status()
        return response.json()


# --- Pure planning logic (unit-tested, no HTTP) --------------------------------

def already_registered(existing: list[dict], target: str, event: str, url: str) -> bool:
    """True if `existing` already has a subscription for this exact
    (target, event, url) triple. Matching on url too, not just (target,
    event), so pointing the webhook at a new URL (e.g. a domain migration)
    is visible as "missing" rather than silently treated as already done."""
    return any(
        sub.get("target") == target and sub.get("event") == event and sub.get("url") == url
        for sub in existing
    )


def plan_registrations(existing: list[dict], url: str) -> list[tuple]:
    """Returns the (target, event) pairs that still need registering against
    `url` -- i.e. every entry in EVENTS not already covered by `existing`."""
    return [
        (TARGET, event) for event in EVENTS
        if not already_registered(existing, TARGET, event, url)
    ]


# --- CLI ------------------------------------------------------------------

def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_INGEST_URL,
                         help=f"Ingest URL Copper should call (default: {DEFAULT_INGEST_URL}).")
    parser.add_argument("--commit", action="store_true",
                         help="Actually create the missing subscriptions in Copper (default: dry-run/report-only).")
    args = parser.parse_args(argv)

    if not settings.copper_webhook_secret:
        print("COPPER_WEBHOOK_SECRET is not set -- refusing to register webhooks Copper "
              "would sign with a secret we can't verify against.")
        return 1

    print("Fetching existing Copper webhook subscriptions...")
    existing = list_subscriptions()
    if existing:
        print(f"Found {len(existing)} existing subscription(s):")
        for sub in existing:
            print(f"  id={sub.get('id')} target={sub.get('target')} event={sub.get('event')} url={sub.get('url')}")
    else:
        print("Found 0 existing subscriptions.")

    missing = plan_registrations(existing, args.url)
    if not missing:
        print(f"\nAll {len(EVENTS)} `{TARGET}` subscriptions already registered against {args.url}. Nothing to do.")
        return 0

    print(f"\n{'Would create' if not args.commit else 'Creating'} {len(missing)} subscription(s) "
          f"against {args.url}:")
    for target, event in missing:
        print(f"  {target} / {event}")

    if not args.commit:
        print("\nDry run -- pass --commit to actually register these with Copper.")
        return 0

    for target, event in missing:
        created = create_subscription(target, event, args.url, settings.copper_webhook_secret)
        print(f"  registered {target}/{event} -> id={created.get('id')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
