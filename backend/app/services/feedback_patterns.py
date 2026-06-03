"""
Feedback → pattern loop.

Turns the investment team's thumbs (👍/👎) and bucket overrides — captured in
`assessment_overrides` — into retrievable *calibration exemplars* that get
injected into future assessments. This is the living complement to the static
portfolio retrospective (raed_portfolio_v2.json): the portfolio teaches what
past *bets* taught us; these exemplars teach how the team is judging *new* deals
right now. Where the two conflict, the recent team calls win.

No model retraining — the loop closes through retrieval + prompt injection, the
same mechanism as the portfolio precedents.
"""
from __future__ import annotations

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.override import AssessmentOverride
from app.models.lead import Lead
from app.services.portfolio_retrieval import _keywords


# Triggers that carry a usable human verdict. "confirm"/"approve" = the team
# endorsed the AI bucket; "override"/"re-override" = the team set a different
# bucket; "rate_down" = the team flagged the AI bucket as wrong (a caution, no
# positive label). "skip" is excluded — it's an archive action, not a judgment.
_USABLE_TRIGGERS = {"confirm", "approve", "override", "re-override", "rate_down"}


async def retrieve_labeled_exemplars(
    db: AsyncSession, lead_text: str, k: int = 4, pool: int = 400, exclude_lead_id=None
) -> list[dict]:
    """Return up to `k` past team-labeled leads most similar to `lead_text`.

    We pull the most recent `pool` override rows, keep only the latest verdict
    per lead, then rank by keyword overlap with the new lead. Cheap (no
    embeddings) and consistent with portfolio_retrieval's matching.

    `exclude_lead_id` drops the lead currently being assessed, so re-assessing a
    lead learns from *other* leads' verdicts rather than parroting its own.
    """
    rows = (
        await db.execute(
            select(AssessmentOverride, Lead.company_name)
            .join(Lead, AssessmentOverride.lead_id == Lead.id)
            .order_by(desc(AssessmentOverride.created_at))
            .limit(pool)
        )
    ).all()

    lead_kw = _keywords(lead_text)
    if not lead_kw:
        return []

    seen: set = set()
    scored: list[tuple[int, dict]] = []
    for ov, company in rows:
        if exclude_lead_id is not None and ov.lead_id == exclude_lead_id:
            continue
        if ov.lead_id in seen:
            continue  # keep only the most recent verdict per lead
        seen.add(ov.lead_id)
        if ov.trigger not in _USABLE_TRIGGERS:
            continue
        text = " ".join(filter(None, [ov.ai_summary or "", ov.deck_excerpt or ""]))[:2000]
        overlap = len(lead_kw & _keywords(text))
        if overlap <= 0:
            continue
        scored.append(
            (
                overlap,
                {
                    "company": company,
                    "ai_bucket": ov.ai_bucket,
                    "human_bucket": ov.human_bucket,
                    "trigger": ov.trigger,
                    "reason": (ov.human_reason or "").strip() or None,
                    "reason_tags": ov.human_reason_tags or None,
                },
            )
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:k]]


def format_for_prompt(exemplars: list[dict]) -> str:
    """Render exemplars as the markdown block injected into the assess prompt."""
    if not exemplars:
        return "(no recent team calibration on similar leads yet)"

    lines = []
    for e in exemplars:
        trig = e.get("trigger")
        if trig == "confirm":
            verdict = f"team CONFIRMED the AI's {e['human_bucket']}"
        elif trig == "approve":
            verdict = f"team APPROVED ({e['human_bucket']})"
        elif trig in ("override", "re-override"):
            verdict = f"team CORRECTED {e['ai_bucket']} → {e['human_bucket']}"
        elif trig == "rate_down":
            verdict = f"team flagged the AI's {e['ai_bucket']} as WRONG"
        else:
            verdict = e.get("human_bucket") or "?"

        reason = e.get("reason")
        tags = ", ".join(e.get("reason_tags") or []) if e.get("reason_tags") else ""
        why = f" — \"{reason}\"" if reason else (f" — {tags}" if tags else "")
        lines.append(f"- **{e.get('company')}**: AI said {e.get('ai_bucket')}; {verdict}{why}")
    return "\n".join(lines)
