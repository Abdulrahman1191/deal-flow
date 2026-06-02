import json
from typing import Any

from openai import OpenAI

from app.config import settings

_PLACEHOLDER = "[Associate Name]"


def _substitute_name(data: dict) -> None:
    """Replace [Associate Name] placeholder in draft fields with the configured name."""
    name = settings.associate_name
    for key in ("draft_body", "draft_subject"):
        if isinstance(data.get(key), str):
            data[key] = data[key].replace(_PLACEHOLDER, name)

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.deep_seek_api,
            base_url="https://api.deepseek.com",
        )
    return _client


ASSESS_SYSTEM = """You are a senior investment analyst at Raed Ventures, a sector-agnostic early-stage
venture fund investing in deep tech companies across the MENA region.

Your job is to research a company and produce a structured investment assessment.
You will receive structured research data gathered from the web (company website content,
LinkedIn profiles, Crunchbase data, news, patents) plus, when available, the founder's
own pitch deck.

Think like a thoughtful investor, not a filter. Most leads will lack perfect information —
that is normal early-stage venture work, not grounds for an automatic rejection. Your
default response to thin data is "MAYBE — flag for review", not "REJECT". REJECT is only
for cases where the evidence affirmatively shows a poor fit, not for cases where you
couldn't find enough evidence to decide.

Surface nuance. Flag what you don't know in `data_gaps`. Cite sources in `research_sources`.

Return your output as a valid JSON object matching the AssessmentResult schema."""

ASSESS_USER_TEMPLATE = """Research the following company and produce a full investment assessment.

Company: {company_name}
Website: {website}
Company LinkedIn: {company_linkedin_url}
Description: {description}
Stage: {stage}
Region: {region}
Founders: {founder_names}
Founder LinkedIn(s): {linkedin_urls}

Research data:
{research_data}

Pitch deck excerpt (founder-provided, prefer this over web research when they conflict):
{pitch_deck_excerpt}

Investment Criteria — scoring rubrics
Score each criterion using the rubric below. Total = sum of criterion scores (0-100).

1. **MENA focus** (0-20): operating in or targeting the MENA region
   - 20: HQ or core operations in MENA (KSA, UAE, Egypt, Jordan, Lebanon, Morocco, Tunisia, Bahrain, Kuwait, Oman, Qatar) AND MENA-targeted customers/market
   - 15: Founder is from MENA region OR explicitly states MENA as primary market, even if currently elsewhere
   - 10: MENA is one of several target markets, or expansion is planned but unconfirmed
   - 5: MENA mentioned but not core to the business
   - 0: No MENA connection found

2. **Deep technology** (0-25): proprietary, defensible technology that's hard to replicate
   - 25: Clear novel technical approach with technical detail in deck / website (e.g., custom hardware, novel ML architecture, biological/chemical IP, materials science)
   - 20: Strong technical differentiation but well-trodden domain (e.g., LLM-fine-tuning on specialised data, advanced computer vision pipeline)
   - 15: Some technical depth but the "secret sauce" isn't clear from materials
   - 10: Mostly applied / integration work on top of off-the-shelf tech, but in a hard domain
   - 5: Basic web/mobile product that uses commodity AI APIs or standard SaaS patterns
   - 0: No technical depth — clear marketplace, dropshipping, agency, or pure CRUD app

3. **Strong IP / moat** (0-20): patents, proprietary data, hardware, network effects, regulatory
   - 20: Granted patents, proprietary datasets, hardware moats, or hard-won regulatory licences
   - 15: Filed patents pending, or a defensible data flywheel in motion
   - 10: First-mover advantage in a niche, but no patents yet
   - 5: Some defensibility claimed but no evidence
   - 0: Pure execution play, easily copied

4. **Team experience** (0-20): domain expertise + prior-startup or relevant operating background
   - 20: Founder(s) have PhD in relevant field, prior exit, or 10+ years senior at relevant operator
   - 15: Founder(s) have strong domain credentials (5+ years senior at relevant operator, or notable academic background) OR prior startup with traction
   - 10: Founder(s) are credentialed but the credentials don't match the company's claimed technical depth
   - 5: First-time founders without obvious domain expertise
   - 0: Clear domain mismatch (e.g., founder of a "biotech" startup has only e-commerce background)

5. **Stage alignment** (0-10): pre-seed to Series A only
   - 10: Pre-seed or seed, looking to raise within next 6 months
   - 7: Series A or recently raised seed, still in our window
   - 3: Stage unclear or transitioning past Series A
   - 0: Series B or later

6. **Model fit** (0-5): NOT a traditional marketplace or basic SaaS
   - 5: Deep tech business model (sells a technical product / API / hardware / regulated service)
   - 3: SaaS but with meaningful technical moat
   - 1: Marketplace with a technology layer that materially changes the unit economics
   - 0: Pure marketplace, dropshipping, agency, or basic SaaS

Bucket assignment based on total score:
- **80-100 → YES** (schedule a meeting — strong fit)
- **50-79 → MAYBE** (flag for human review — interesting but unclear)
- **0-49 → REJECT** (poor fit on multiple dimensions)

Important guardrails — read these CAREFULLY before scoring:

1. **A score of 0 means "I have AFFIRMATIVE EVIDENCE that this criterion fails."** It does NOT
   mean "I couldn't find data." If your reasoning would read "no information / not found /
   cannot confirm / cannot rule out", the correct score is in the **5-8 range** with the
   uncertainty surfaced as a `data_gaps` entry. Reserve 0 for cases where the evidence is
   clear and negative (e.g., "the founder's LinkedIn shows 15 years in e-commerce, not biotech,
   contradicting the deep-tech-bio claim").

2. **DEFAULT TO MAYBE WHEN THE PICTURE IS THIN.** If 3+ of the 6 criteria have data_gaps,
   you almost certainly should output `MAYBE`, not `REJECT`. REJECT is for cases where the
   evidence we DO have shows poor fit, not for cases where evidence is sparse.

3. **The total-score → bucket mapping has a safety override:** if `len(data_gaps) >= 3` and
   the raw total would land in REJECT (< 50), bump the bucket to MAYBE. A human reviewer is
   better placed than you to make a final call when so much is unknown.

4. **Arabic / non-English company names with sparse English-language web data should not be
   penalised on MENA focus.** Score MENA focus 15+ if the name, language, or domain (.sa, .ae,
   .eg, .jo, .ma, .tn, .bh, .om, .kw, .qa, .lb) indicates MENA, even if specific operations
   couldn't be verified.

5. **Distinguish "founder from MENA" from "no MENA presence found":** founder-from-MENA → 15;
   no-MENA-anywhere → 0. Don't conflate them.

Return a JSON object with this exact structure:
{{
  "summary": "one-paragraph company synthesis",
  "bucket": "YES" | "MAYBE" | "REJECT",
  "confidence_score": <integer 0-100>,
  "scoring_breakdown": {{
    "mena_focus":      {{ "score": <0-20>, "reasoning": "..." }},
    "deep_tech":       {{ "score": <0-25>, "reasoning": "..." }},
    "strong_ip":       {{ "score": <0-20>, "reasoning": "..." }},
    "team_experience": {{ "score": <0-20>, "reasoning": "..." }},
    "stage_alignment": {{ "score": <0-10>, "reasoning": "..." }},
    "model_fit":       {{ "score": <0-5>,  "reasoning": "..." }}
  }},
  "positive_signals": ["..."],
  "red_flags": ["..."],
  "data_gaps": ["..."],
  "research_sources": ["url"],
  "draft_type": "rejection" | "meeting_request" | null,
  "draft_subject": "..." | null,
  "draft_body": "..." | null
}}

For the draft_body:
- If YES: Write a short, genuine email (under 80 words) expressing interest in the company and inviting the founder to book a call. Include this Calendly link: https://calendly.com/abdulrahman-raed/30min. Mention 1 specific thing that caught our attention. Sign off as {associate_name}, Raed Ventures.
- If REJECT: Write a brief, honest email (under 120 words) explaining that the business model doesn't fit what we look for at Raed Ventures right now. Keep the door open — wish them well and encourage them to reach out if things evolve. No bullet points. IMPORTANT: Never cite lack of information or insufficient data as a reason — if data is thin, fall back to a fit-based reason (e.g. stage, sector focus, model type). Sign off as {associate_name}, Raed Ventures.
- If MAYBE: set draft_type, draft_subject, draft_body all to null."""


BRIEFING_SYSTEM = """You are a venture capital intelligence analyst for Raed Ventures, a sector-agnostic
early-stage fund focused on deep tech in the MENA region.

Every morning you research the global and MENA investment landscape and produce
a curated briefing for the investment associate.

Your briefing must be tightly aligned with the fund's thesis:
- Deep technology with defensible IP
- MENA-based or MENA-targeting companies
- Pre-seed to Series A stage
- NOT marketplaces or basic SaaS

Be proactive. Surface what the associate should know today, even if not asked.
Cite your sources. Return valid JSON matching the BriefingResult schema."""

BRIEFING_USER_TEMPLATE = """Today is {date}. Research data:

{research_data}

Produce the daily investment intelligence briefing.

Return a JSON object with this exact structure:
{{
  "top_themes": [
    {{
      "rank": 1,
      "title": "...",
      "description": "2-3 sentence summary",
      "tags": ["deep tech", "MENA", ...],
      "sources": ["url", ...]
    }}
  ],
  "deep_dives": [
    {{
      "title": "...",
      "body": "detailed analysis paragraph",
      "sources": ["url", ...]
    }}
  ]
}}

top_themes must have exactly 5 items ranked by relevance to the Raed Ventures thesis.
deep_dives must have exactly 2 items for the 2 most compelling themes."""


def assess_lead(lead_data: dict, research_data: dict) -> dict[str, Any]:
    pitch_deck_text = lead_data.get("pitch_deck_text") or ""
    pitch_deck_excerpt = pitch_deck_text[:12_000] if pitch_deck_text else "(none provided)"

    prompt = ASSESS_USER_TEMPLATE.format(
        associate_name=settings.associate_name,
        company_name=lead_data.get("company_name", ""),
        website=lead_data.get("website", "N/A"),
        company_linkedin_url=lead_data.get("company_linkedin_url") or "N/A",
        description=lead_data.get("description", "N/A"),
        stage=lead_data.get("stage", "N/A"),
        region=lead_data.get("region", "N/A"),
        founder_names=", ".join(lead_data.get("founder_names") or []) or "N/A",
        linkedin_urls=", ".join(lead_data.get("linkedin_urls") or []) or "N/A",
        research_data=json.dumps(research_data, indent=2),
        pitch_deck_excerpt=pitch_deck_excerpt,
    )

    response = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": ASSESS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    _enforce_bucket_consistency(result)
    _substitute_name(result)
    return result


def _enforce_bucket_consistency(result: dict) -> None:
    """Defensive guardrails that the LLM doesn't always honour from the prompt.

    1. If the breakdown sums to YES territory (80+) but the LLM picked something
       lower, bump bucket to YES. Conversely if it sums to REJECT but model said YES.
    2. If `data_gaps` has 3+ entries AND the model said REJECT, bump to MAYBE —
       a human reviewer should weigh in when the picture is this thin.
    3. Sync the draft_type field with the final bucket.
    """
    breakdown = result.get("scoring_breakdown") or {}
    try:
        total = sum(int((v or {}).get("score") or 0) for v in breakdown.values())
    except Exception:
        total = 0

    bucket = (result.get("bucket") or "").upper()
    gaps = result.get("data_gaps") or []

    # 1. Realign bucket with the numeric total (LLMs sometimes pick the wrong tier)
    if total >= 80:
        bucket = "YES"
    elif total >= 50:
        bucket = "MAYBE"
    else:
        bucket = "REJECT"

    # 2. Sparse-data safety net: thin picture → MAYBE, not REJECT
    if bucket == "REJECT" and isinstance(gaps, list) and len(gaps) >= 3:
        bucket = "MAYBE"
        red_flags = result.get("red_flags") or []
        if not any("safety override" in (f or "").lower() for f in red_flags):
            red_flags.append(
                "Bucket bumped to MAYBE by safety override (3+ data gaps — human reviewer should weigh in)."
            )
            result["red_flags"] = red_flags

    result["bucket"] = bucket

    # 3. Draft type must match bucket
    if bucket == "YES":
        result["draft_type"] = "meeting_request"
    elif bucket == "REJECT":
        result["draft_type"] = "rejection"
    else:  # MAYBE
        result["draft_type"] = None
        result["draft_subject"] = None
        result["draft_body"] = None


DRAFT_REGEN_SYSTEM = """You are a senior investment associate at Raed Ventures.
Write a single outbound email to the founder based on the target decision (YES, MAYBE, REJECT)."""

DRAFT_REGEN_USER_TEMPLATE = """The investment team has manually decided this lead is **{bucket}**.
This decision is FINAL — your only job is to write the email that matches this decision.
Do NOT re-evaluate the company. Do NOT switch the decision based on the context below.

Company: {company_name}
Founder: {founder_names}
Background context (for tone only, not for re-deciding): {summary}

Rules per bucket — you MUST follow these exactly:
- YES: draft_type MUST be "meeting_request". Short, genuine email (under 80 words) expressing
  interest and inviting the founder to book a call via https://calendly.com/abdulrahman-raed/30min.
  Mention 1 specific thing about the company that caught our attention (extrapolate positively
  from the context if needed). Sign off as {associate_name}, Raed Ventures.
- MAYBE: draft_type, draft_subject, draft_body MUST all be null. Do not write an email.
- REJECT: draft_type MUST be "rejection". Brief, honest email (under 120 words) explaining
  the business model doesn't fit what we look for at Raed Ventures right now. Keep the door
  open — wish them well, encourage them to reach out if things evolve. No bullet points.
  IMPORTANT: Never cite lack of information or insufficient data as a reason — always use a
  fit-based reason (stage, sector focus, model type). Sign off as {associate_name}, Raed Ventures.

Return strict JSON:
{{
  "draft_type": "meeting_request" | "rejection" | null,
  "draft_subject": "..." | null,
  "draft_body": "..." | null
}}"""


PICK_LINKEDIN_SYSTEM = """You are a research assistant disambiguating a company's
official LinkedIn page from a candidate list. Be conservative: if no candidate
clearly matches the company described, return null. Do not guess."""

PICK_LINKEDIN_USER_TEMPLATE = """Company: {company_name}
Founder(s): {founder_names}
Description: {description}
Region: {region}

Candidates (LinkedIn URLs found in search results):
{candidates_list}

Pick the single URL that most likely belongs to THIS company. Lean on founder
names and description for disambiguation. If multiple companies share the name
and none clearly match the context, return null.

Return strict JSON: {{"url": "<chosen URL>" | null}}"""


def pick_linkedin_url(
    company_name: str,
    founder_names: list,
    description: str,
    region: str,
    candidates: list,
) -> Any:
    """Ask DeepSeek to pick the right LinkedIn URL from a candidate list."""
    if not candidates:
        return None
    prompt = PICK_LINKEDIN_USER_TEMPLATE.format(
        company_name=company_name,
        founder_names=", ".join(founder_names) or "unknown",
        description=(description or "unknown")[:400],
        region=region or "unknown",
        candidates_list="\n".join(f"- {c}" for c in candidates),
    )
    try:
        response = _get_client().chat.completions.create(
            model=settings.deepseek_model,
            max_tokens=200,
            messages=[
                {"role": "system", "content": PICK_LINKEDIN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        url = data.get("url")
        # Only accept URLs that were actually in the candidate list
        if url and url in candidates:
            return url
    except Exception as e:
        print(f"[pick_linkedin_url] failed: {e!r}")
    return None


def regenerate_draft(lead_data: dict, bucket: str, summary: str = "") -> dict[str, Any]:
    """Produces a fresh draft email for a manually-set bucket. No re-assessment."""
    if bucket not in ("YES", "MAYBE", "REJECT"):
        raise ValueError(f"bucket must be YES/MAYBE/REJECT, got {bucket!r}")

    prompt = DRAFT_REGEN_USER_TEMPLATE.format(
        associate_name=settings.associate_name,
        bucket=bucket,
        company_name=lead_data.get("company_name", ""),
        founder_names=", ".join(lead_data.get("founder_names") or []) or "N/A",
        summary=summary or "(no prior summary)",
    )
    response = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": DRAFT_REGEN_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content)
    # Defensive: enforce draft_type matches the requested bucket, regardless of LLM output.
    if bucket == "YES":
        result["draft_type"] = "meeting_request"
    elif bucket == "REJECT":
        result["draft_type"] = "rejection"
    else:  # MAYBE
        result["draft_type"] = None
        result["draft_subject"] = None
        result["draft_body"] = None
    _substitute_name(result)
    return result


def generate_briefing(date_str: str, research_data: dict) -> dict[str, Any]:
    prompt = BRIEFING_USER_TEMPLATE.format(
        date=date_str,
        research_data=json.dumps(research_data, indent=2),
    )

    response = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=8096,
        messages=[
            {"role": "system", "content": BRIEFING_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)
