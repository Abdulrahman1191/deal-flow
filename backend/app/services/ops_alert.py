"""
Page the operations owner through reem's ops-alert path (POST /internal/ops/alert).

That is the one route an operations fault takes to a human's WhatsApp: one
bubble, action first, detail after, deduplicated per key on reem's side so a
fault that keeps happening pages once per cooldown, whoever reports it.

Best effort by design: posted from a background thread with a short timeout,
never raises, never blocks the caller. When the route is not configured
(REEM_OPS_ALERT_URL / REEM_INTERNAL_TOKEN empty) the page is printed to the
log instead, so the fault is at least visible there.
"""
from __future__ import annotations

import json
import threading
import urllib.request

from app.config import settings

SOURCE = "deal-flow"


def page(key: str, action: str, detail: str = "", severity: str = "warn") -> bool:
    """Send one ops alert. Returns False when the route is not configured (logged instead)."""
    url, token = settings.reem_ops_alert_url, settings.reem_internal_token
    if not url or not token:
        print(f"[ops_alert] not configured, would page ({key}): {action} | {detail}")
        return False
    body = json.dumps({"key": key, "action": action, "detail": detail, "source": SOURCE, "severity": severity}).encode()

    def _post() -> None:
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
            })
            with urllib.request.urlopen(req, timeout=10) as res:
                res.read()
        except Exception as exc:  # a failed page must never break the caller
            print(f"[ops_alert] page failed ({key}): {exc!r}")

    threading.Thread(target=_post, daemon=True).start()
    return True
