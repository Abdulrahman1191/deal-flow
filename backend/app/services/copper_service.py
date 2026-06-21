from __future__ import annotations
"""
Copper CRM API client.
Fetches "My Open Leads" (leads with status=Open AND assignee=our user) and maps them.
Auth: API key + user email in headers (Copper developer API format).
"""
from typing import Optional

import httpx

from app.config import settings

COPPER_BASE = "https://api.copper.com/developer_api/v1"
PAGE_SIZE = 200


def _headers() -> dict:
    return {
        "X-PW-AccessToken": settings.copper_api_key,
        "X-PW-Application": "developer_api",
        "X-PW-UserEmail": settings.copper_user_email,
        "Content-Type": "application/json",
    }


def fetch_open_leads_for_user(copper_user_id: Optional[int] = None) -> list[dict]:
    """
    Fetches all open leads assigned to a Copper user, paginating until exhausted.
    Returns a list of raw Copper lead dicts.

    The shared Copper API key can read the whole account, so we filter by
    `assignee_ids` to pull only the leads connected to `copper_user_id`. When
    omitted, falls back to the configured `settings.copper_user_id` (back-compat).
    """
    assignee_id = copper_user_id or settings.copper_user_id
    if not assignee_id or not settings.copper_open_status_id:
        raise RuntimeError(
            "copper_user_id (or COPPER_USER_ID) and COPPER_OPEN_STATUS_ID must be set. "
            "Run: python scripts/bootstrap_copper.py"
        )

    all_leads: list[dict] = []
    page = 1

    with httpx.Client(timeout=30) as client:
        while True:
            body = {
                "page_size": PAGE_SIZE,
                "page_number": page,
                "assignee_ids": [assignee_id],
                "status_ids": [settings.copper_open_status_id],
                "sort_by": "date_created",
                "sort_direction": "desc",
            }
            response = client.post(
                f"{COPPER_BASE}/leads/search",
                headers=_headers(),
                json=body,
            )
            response.raise_for_status()
            batch = response.json()
            all_leads.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1

    return all_leads


def fetch_lead_by_id(copper_id: str) -> Optional[dict]:
    """Fetch a single Copper Lead by its ID. Returns None if not found (404).

    Used by the inbound `Lead.updated` webhook handler to grab the current
    state of a lead after an external edit, so we can reconcile our local row.
    """
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{COPPER_BASE}/leads/{copper_id}", headers=_headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


def lookup_user_id(email: str) -> int:
    """Look up the Copper user_id for a given email (one-time bootstrap)."""
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{COPPER_BASE}/users/search",
            headers=_headers(),
            json={"emails": [email], "page_size": 10},
        )
        response.raise_for_status()
        users = response.json()
        if not users:
            raise RuntimeError(f"No Copper user found for email {email}")
        return int(users[0]["id"])


def lookup_status_id_by_name(name: str) -> int:
    """Look up a lead status_id by its name (case-insensitive)."""
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{COPPER_BASE}/lead_statuses", headers=_headers())
        response.raise_for_status()
        for s in response.json():
            if str(s.get("name", "")).strip().lower() == name.strip().lower():
                return int(s["id"])
    raise RuntimeError(f"No status named {name!r} found.")


def lookup_pipeline_and_first_stage(pipeline_name: str) -> tuple:
    """Returns (pipeline_id, first_stage_id) for the pipeline matching pipeline_name."""
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{COPPER_BASE}/pipelines", headers=_headers())
        response.raise_for_status()
        for p in response.json():
            if str(p.get("name", "")).strip().lower() == pipeline_name.strip().lower():
                stages = p.get("stages", [])
                if not stages:
                    raise RuntimeError(f"Pipeline {pipeline_name!r} has no stages.")
                return int(p["id"]), int(stages[0]["id"])
    raise RuntimeError(f"No pipeline named {pipeline_name!r} found.")


def lookup_open_status_id() -> int:
    """
    Look up the numeric status_id for Copper's default lead status (one-time bootstrap).
    Copper's "My Open Leads" view filters to leads with the default status (typically
    named "New" or "Open"), so we pick the entry with is_default=True.
    """
    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{COPPER_BASE}/lead_statuses",
            headers=_headers(),
        )
        response.raise_for_status()
        statuses = response.json()
        for s in statuses:
            if s.get("is_default"):
                return int(s["id"])
        raise RuntimeError(
            f"No default status found. Available: {[s.get('name') for s in statuses]}"
        )


def map_copper_lead(p: dict) -> dict:
    """Maps a raw Copper lead dict to our Lead model fields."""
    emails = p.get("email", []) or []
    if isinstance(emails, list):
        recipient_email = emails[0].get("email", "") if emails else ""
    else:
        recipient_email = emails.get("email", "") if isinstance(emails, dict) else ""

    websites = p.get("websites", []) or []
    if isinstance(websites, list):
        website = websites[0].get("url", "") if websites else ""
    else:
        website = websites.get("url", "") if isinstance(websites, dict) else ""

    company = p.get("company_name") or (p.get("company") or {}).get("name") or p.get("name", "Unknown")

    tags = p.get("tags") or []
    stage = tags[0] if tags else None

    socials = p.get("social_profiles") or []
    company_linkedin_url: Optional[str] = None
    founder_linkedin_urls: list = []
    for s in socials:
        url = s.get("url", "")
        lower = url.lower()
        if "linkedin.com" not in lower:
            continue
        if "/company/" in lower or "/school/" in lower:
            if not company_linkedin_url:
                company_linkedin_url = url
        else:
            founder_linkedin_urls.append(url)

    return {
        "copper_id": str(p.get("id", "")),
        "company_name": company,
        "website": website or None,
        "description": p.get("details") or p.get("description"),
        "stage": stage,
        "region": None,
        "founder_names": [p["name"]] if p.get("name") else None,
        "linkedin_urls": founder_linkedin_urls or None,
        "company_linkedin_url": company_linkedin_url,
        "raw_copper_data": {**p, "recipient_email": recipient_email},
    }
