"""Shared language-mismatch / missing-draft detection (issue #177).

Both the assessment read model (app/routers/assessments.py) and
scripts/regenerate_mismatched_drafts.py need the same two checks:

  language_mismatch -- does the applicant's detected language
                        (claude_agent.detect_applicant_language) disagree
                        with the script the current draft_body is actually
                        written in (claude_agent.detect_draft_script)? Only
                        meaningful when a draft exists.
  draft_missing     -- does the effective bucket (YES/REJECT) require a
                        draft that isn't there? Always False for MAYBE --
                        a MAYBE legitimately has no draft (the prompt nulls
                        draft_type/draft_subject/draft_body for MAYBE, and
                        claude_agent._enforce_bucket_consistency re-nulls
                        them), so this is a guard against a rare silent gap
                        on YES/REJECT, never a MAYBE "backlog".

Pulled out into its own module (rather than living in routers/assessments.py)
so the backfill script can reuse it without importing the router.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import settings
from app.services import claude_agent, copper_service

_DRAFT_REQUIRED_BUCKETS = ("YES", "REJECT")


def lead_language_fields(lead: Any) -> dict:
    """The subset of a Lead's fields detect_applicant_language reads,
    assembled the same way the assessor / draft-regen call sites build it
    (see app.tasks.assess_lead._run and
    app.routers.assessments._regenerate_draft_for_bucket). Uses getattr with
    defaults so callers can pass a partial lead stand-in (as several test
    fakes across the suite do) without raising."""
    return {
        "company_name": getattr(lead, "company_name", None),
        "description": getattr(lead, "description", None),
        "pitch_deck_text": getattr(lead, "pitch_deck_text", None),
        "source_detail": copper_service.get_custom_field_value(
            getattr(lead, "raw_copper_data", None), settings.copper_cf_source_detail_id
        ),
    }


def language_mismatch(lead: Any, draft_body: Optional[str]) -> bool:
    """True when the applicant's detected language doesn't match the script
    the current draft_body is actually written in. False when there's no
    draft at all -- nothing to compare, so nothing to flag."""
    if not draft_body:
        return False
    detected = claude_agent.detect_applicant_language(lead_language_fields(lead))
    actual = claude_agent.detect_draft_script(draft_body)
    return detected != actual


def draft_missing(effective_bucket: Optional[str], draft_body: Optional[str]) -> bool:
    """True only when the effective bucket requires a draft (YES/REJECT) and
    none is present. Always False for MAYBE (see module docstring)."""
    return effective_bucket in _DRAFT_REQUIRED_BUCKETS and not draft_body
