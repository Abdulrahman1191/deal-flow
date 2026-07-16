from __future__ import annotations

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
You will receive:
  1. Structured research data gathered from the web (company website content,
     LinkedIn profiles, Crunchbase data, news, patents)
  2. When available, the founder's own pitch deck
  3. **Historical precedents** — 5-8 Raed portfolio companies most similar to this
     lead, with their Original Thesis (with kill criterion), what happened after
     we invested, the named mental models applied, the Hindsight Verdict, and
     transferable Lessons. **Rows marked DISAGREEMENT are the highest-signal
     training cases — they teach what we systematically mis-underwrite, which
     matters more than agreement rows that may be diagnostic of luck.**

How to use precedents (the disciplined 7-step workflow):

  Step 1. Treat each precedent as a structured case. Identify the 1-2 closest
          analogues to the new lead.

  Step 2. **Cross-check the precedents' kill criteria against the new deal.**
          Each Original Thesis ends with "we're wrong if [killer condition]."
          If any of those killer conditions are visibly true in the new lead,
          flag it explicitly in `red_flags`.

  Step 3. **Apply the named mental models** from the matched precedents.
          If a similar past deal failed under "Hubristic Financing" or "Asset-light
          freight is structural-margin-failure", price the same risk into the
          new opportunity.

  Step 4. **Pay extra attention to the DISAGREEMENT precedents.** If the new
          deal's pitch resembles the original rationale on a disagreement row,
          that's a yellow flag — investigate whether the same gap (between
          rationale and what actually happened) could repeat here.

  Step 5. Score the new lead against the 6-criterion rubric below. Use the
          precedents as calibration — if similar past deals scored X on
          deep_tech and ended up failing, don't over-credit the same kind of
          pitch here.

  Step 6. Apply Raed's 3 filters explicitly (Founder Obsession, Market Scale,
          Unfair Advantage) — these are the local layer that sits on top of
          the global rubric.

  Step 7. Surface the citations: in `positive_signals` and `red_flags`, where
          relevant, refer to the precedent companies by name (e.g., "Egypt
          single-country FX risk — same pattern as Trella/Grinta/Sary").

Think like a thoughtful investor reasoning BY ANALOGY, not a scoring machine. Your
decision is NOT the output of a fixed formula. It is a judgment about which of the
labelled historical precedents above this new lead most resembles, and what those
bets taught us.

Anchor every decision in the portfolio's core discipline: **decision quality is
separate from outcome.** Judge whether the signals knowable now fit the pattern of
bets whose theses held up (Hindsight Verdict STRONG YES / YES, Performance
OUTPERFORMING / ON-TRACK) versus bets where our rationale turned out wrong
(Hindsight Verdict MIXED / NO, WRITE-OFF, or a DISAGREEMENT row). A lead that
repeats the failure mechanism of a MIXED / NO / disagreement precedent — the same
gap between pitch and reality — is a deal we have learned to be cautious about,
even when it looks exciting on the surface.

Most early leads lack complete information — that is normal venture work, not
grounds for rejection. Distinguish sharply between "the evidence we DO have shows a
poor fit" (→ REJECT) and "we could not find enough evidence to decide" (→ MAYBE).
Never reject a lead for missing data alone.

Surface nuance. Flag what you don't know in `data_gaps`. Cite sources in
`research_sources`. **When precedents inform a signal, name them.**

Return your output as a valid JSON object matching the AssessmentResult schema."""

ASSESS_USER_TEMPLATE = """Research the following company and produce a full investment assessment.

Company: {company_name}
Website: {website}
Company LinkedIn: {company_linkedin_url}
Description: {description}
Stage: {stage}
Region: {region}
CRM contact: {founder_names}
  (⚠ This is whoever the inbound lead record names — often an employee, advisor, or
  whoever submitted the form. It is NOT verified to be a founder. Verify actual
  founders from the pitch deck team slide and research before crediting anyone.)
Contact LinkedIn(s): {linkedin_urls}

Research data:
{research_data}

Pitch deck excerpt (founder-provided, prefer this over web research when they conflict):
{pitch_deck_excerpt}

Historical precedents from Raed's portfolio (most similar to this lead):
{precedents_block}

Recent team calibration — how Raed's investment team has actually judged SIMILAR
leads lately (👍/👎 and bucket overrides). This is the most current signal of our
taste. **Where it conflicts with the historical portfolio precedents above, trust
THIS** — and if one of these closely matches the lead, weight it heavily:
{team_calibration_block}

Considerations checklist — score each criterion below as supporting evidence that
DOCUMENTS your reasoning on the assessment card. These scores do NOT mechanically
decide the bucket; the decision is the holistic, pattern-based judgment described in
"How to decide the bucket" further down. A lead can score moderately yet be a clear
REJECT (it repeats a known failure pattern) or a clear YES (it matches our strongest
bets on the dimension that actually mattered).

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

How to decide the bucket — by analogy to the labelled precedents above, NOT by summing scores:

- **YES — Worth a meeting (this is NOT a funding decision).** The lead shows a *credible*
  thesis-fit signal that earns Raed's time to learn more: at least one real moat marker is
  plausibly present — genuine applied or novel AI in a hard domain (e.g. medical imaging,
  multi-agent systems, legal/clinical reasoning), proprietary data, hardware, or a regulatory
  licence / unfair advantage — together with a MENA angle and roughly the right stage. You do
  NOT need concrete proof on all three Raed filters; early inbound rarely has that, and a meeting
  is exactly how we'd find out. You need enough genuine signal that meeting the founder would
  teach us something. Calibrate to the winning precedents (Hindsight Verdict STRONG YES / YES,
  Performance OUTPERFORMING / ON-TRACK): a lead that shares their *kind* of moat belongs here.
  "I want to see this" → YES.

- **REJECT** — Affirmative poor fit, established on evidence we DO have. EITHER (a) the lead
  repeats the failure mechanism of a MIXED / NO / WRITE-OFF / DISAGREEMENT precedent — the
  same gap between pitch and reality — OR (b) it plainly fails a Raed filter: no unfair
  advantage that compounds, no genuine deep tech (pure marketplace / agency / services /
  commodity SaaS / real estate), a market too small for GCC exit math, or outside our
  pre-seed→Series A window. REJECT is about poor FIT, never about missing data.

- **MAYBE — Genuinely ambiguous; not clearly worth a meeting yet.** The thesis-fit signal is
  weak or unresolved — e.g. an integration / RPA wrapper, a commodity-AI feature with no
  defensible angle, or a fit that hinges on facts we simply cannot establish. Route to human
  review. Do NOT use MAYBE as a safe hedge: a lead with a credible moat signal is a YES, and a
  lead with clear poor fit is a REJECT. Reserve MAYBE for the genuinely in-between.

Decision discipline — read before deciding:

**Be decisive — do not park promising leads at MAYBE.** YES is a meeting, not a cheque, so a
credible deep-tech / hardware / regulatory-moat angle with thesis fit should be YES even on thin
data. Before landing on MAYBE, ask: is there a real moat signal (→ YES) or a clear anti-pattern
(→ REJECT)? Only choose MAYBE when genuinely neither is true. A bucket of all-MAYBE is a failure.

1. **Decision quality ≠ outcome.** Weight DISAGREEMENT precedents heaviest — they are where
   Raed has learned what it systematically mis-underwrites. If this lead's pitch echoes the
   original rationale of a disagreement row, treat it as a red flag and say so in `red_flags`,
   naming the precedent.

2. **A criterion score of 0 means AFFIRMATIVE negative evidence**, not "couldn't find data."
   If your reasoning reads "not found / cannot confirm", score 5-8 and log a `data_gaps`
   entry — never let absence of data, by itself, push a lead toward REJECT.

2b. **VERIFY FOUNDER IDENTITY — never assume the CRM contact is the founder.** The contact
   on the lead is frequently an employee, a fundraising advisor, or a form-submitter.
   Establish who actually founded the company from the pitch deck's team slide and the
   research data (LinkedIn, news). Rules:
   - Score `team_experience` ONLY on people verified as founders/co-founders. If the only
     person you know is the unverified CRM contact, score 5-8 and log a `data_gaps` entry
     ("founder identity unverified") — do not invent a founder profile around the contact.
   - If research shows the contact holds a NON-founder title (e.g. sales manager, analyst),
     say so explicitly in the summary and do NOT describe them as founder/CEO anywhere.
   - If the deck and research name different founders than the contact, use the deck/research
     names and note the discrepancy.
   - In the `summary`, attribute roles only as evidenced ("contact: X; founders per deck: Y").

3. **Credibility red flags OVERRIDE the thin-data default → REJECT.** Missing data alone never
   justifies REJECT — but *affirmative* credibility red flags do, even when the overall picture
   is thin. If the founder's identity cannot be verified or appears mismatched/fabricated (e.g.
   the named founder's LinkedIn points to a different person), AND the company has no verifiable
   existence (no website, news, registry, or product), AND the materials are garbled or
   uninformative — that combination is negative evidence about the deal, not a neutral "unknown".
   Output REJECT and name the specific red flags in `red_flags`. Do not hide behind MAYBE.

4. **Arabic / non-English names with sparse English-language web data are NOT penalised on
   MENA focus.** A MENA name, language, or domain (.sa .ae .eg .jo .ma .tn .bh .om .kw .qa .lb)
   satisfies MENA focus even if specific operations couldn't be verified online.

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

For the draft_body — READ THIS FIRST: every lead in this system is INBOUND. The
recipient applied / reached out to Raed Ventures through our website or intake form
(Copper source: "Inbound - Info/Website") — we did not find them, discover them, or
seek them out. The email is a reply to their application, written by a person, not
an AI doing cold outreach. Never imply we came across, discovered, sought out, or
were introduced to them.

BANNED phrasing — never use these or close variants:
"I came across", "came across your company", "caught my eye", "caught our attention",
"I've been following", "we discovered", "reaching out because we noticed",
"stumbled upon", "on my radar".
BANNED hype adjectives:
"thrilled", "excited to see", "impressed by your incredible", "amazing", "incredible",
"blown away". Write plain, genuine sentences a real person would send — not marketing
copy.

- If YES: draft_type "meeting_request". Short, plain, warm email, MAX 50 WORDS, no
  bullet points. Open by thanking them for applying to Raed Ventures. The recipient
  is the CRM contact — greet them by name but do NOT call them "founder" or assign
  any title unless verified. You may add ONE concrete, factual sentence about what
  the company does (drawn from the deck/description) — plain fact only, never
  flattery, and omit it entirely if it isn't clear from the materials. Ask if they'd
  like to book a short call and include this Calendly link:
  https://calendly.com/abdulrahman-raed/30min. Sign off as {associate_name}, Raed
  Ventures. Follow this shape:
    Hi {{name}},
    Thank you for applying to Raed Ventures. We had a look at {{company}} and we're
    interested to learn more about what you're building.
    Would you like to book a short call? {{calendly}}
    Best,
    {associate_name}, Raed Ventures
- If REJECT: draft_type "rejection". Short, honest, respectful email, MAX 70 WORDS,
  no bullet points. Open by thanking them for applying to Raed Ventures. State
  plainly that it isn't a fit for Raed right now, on a FIT basis (stage, sector, or
  business-model type) — not a judgment of the company. Keep the door open — wish
  them well and invite them to reach back out if things evolve. IMPORTANT: Never
  cite lack of information or insufficient data as a reason — if data is thin, fall
  back to a fit-based reason (e.g. stage, sector focus, model type). Sign off as
  {associate_name}, Raed Ventures.
- If MAYBE: set draft_type, draft_subject, draft_body all to null.

Subject lines must be plain and non-salesy, no clickbait, no emoji — e.g. "Your
application to Raed Ventures" or "Thanks for applying to Raed Ventures"."""


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


def assess_lead(
    lead_data: dict,
    research_data: dict,
    team_calibration: list[dict] | None = None,
) -> dict[str, Any]:
    """Assess a lead.

    `team_calibration` is an optional list of recent team-labeled exemplars
    (from app.services.feedback_patterns.retrieve_labeled_exemplars) — the
    feedback→pattern loop. Callers without a DB session (eval scripts) can omit
    it; the prompt then shows "(no recent team calibration yet)".
    """
    pitch_deck_text = lead_data.get("pitch_deck_text") or ""
    pitch_deck_excerpt = pitch_deck_text[:12_000] if pitch_deck_text else "(none provided)"

    # Portfolio retrieval — find the 6 most similar past Raed bets/passes and
    # inject them as historical precedents into the prompt. See
    # app/services/portfolio_retrieval.py and the LLM guide for the workflow.
    #
    # IMPORTANT: lead.description is frequently empty in our data, so matching on
    # it alone makes retrieval return the same generic top-6 for every lead. Build
    # a richer matching text from the deck + research so each lead pulls the
    # precedents that actually resemble it (e.g. a freight marketplace should pull
    # Trella/Grinta/Sary, not fintech rows).
    from app.services import portfolio_retrieval as _pr
    _match_text = " ".join(
        filter(
            None,
            [
                lead_data.get("description") or "",
                pitch_deck_text[:3000],
                str(research_data.get("summary") or "") if isinstance(research_data, dict) else "",
            ],
        )
    )
    precedents = _pr.find_similar(
        {
            "description": _match_text,
            "sector": lead_data.get("stage") or "",  # rough proxy until we have sector tagging
            "region": lead_data.get("region") or "",
        },
        k=6,
    )
    precedents_block = _pr.format_for_prompt(precedents)

    # Feedback→pattern loop: render the team's recent labeled judgments on
    # similar leads (retrieved by the caller, which has the DB session).
    from app.services import feedback_patterns as _fp
    team_calibration_block = _fp.format_for_prompt(team_calibration or [])

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
        precedents_block=precedents_block,
        team_calibration_block=team_calibration_block,
    )

    response = _get_client().chat.completions.create(
        model=settings.deepseek_model,
        max_tokens=4096,
        # Greedy decoding (temperature 0): the assessment is a screening judgment we
        # want reproducible — the same lead should yield the same bucket run-to-run.
        # At higher temperatures borderline leads flip between MAYBE and REJECT,
        # which both confuses users and makes eval deltas unmeasurable. Draft email
        # wording is reviewed/edited before sending, so we don't need sampling variety.
        temperature=0.0,
        messages=[
            {"role": "system", "content": ASSESS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    _enforce_bucket_consistency(result)
    _substitute_name(result)
    # Surface which precedents got cited so we can persist them on the
    # assessment_card and measure their influence later.
    result["precedents_cited"] = [
        {"company": p["company"], "score": p["_score"], "verdict": p.get("hindsight_verdict")}
        for p in precedents
    ]
    return result


def _enforce_bucket_consistency(result: dict) -> None:
    """Light guardrails on the model's pattern-based decision.

    The bucket is now the model's HOLISTIC, analogy-driven judgment (see the prompt).
    We deliberately do NOT recompute it from a score sum, and we do NOT apply a
    data-gap safety override — both of those mechanically suppressed REJECT and made
    the bucket a function of arithmetic rather than fit. We only:

    1. Validate the bucket is one of the three legal values (fall back to MAYBE — the
       human-review bucket — if the model returned something unexpected).
    2. Sync the draft_type / draft fields with the final bucket so a MAYBE never
       carries a stray draft and a YES/REJECT always has the right draft_type.
    """
    bucket = (result.get("bucket") or "").upper()
    if bucket not in ("YES", "MAYBE", "REJECT"):
        bucket = "MAYBE"
    result["bucket"] = bucket

    # Draft type must match bucket
    if bucket == "YES":
        result["draft_type"] = "meeting_request"
    elif bucket == "REJECT":
        result["draft_type"] = "rejection"
    else:  # MAYBE
        result["draft_type"] = None
        result["draft_subject"] = None
        result["draft_body"] = None


DRAFT_REGEN_SYSTEM = """You are a senior investment associate at Raed Ventures.
Write a single email replying to an INBOUND lead, based on the target decision
(YES, MAYBE, REJECT). Every lead in this system applied / reached out to Raed
Ventures — we did not find them, discover them, or seek them out. This is a reply
to their application, written by a person, not an AI doing cold outreach — never
imply we came across, discovered, or were introduced to them.

The recipient is the lead's CRM contact — they may be a founder, an employee, or a
representative, so greet them by name but do NOT address them as "founder" or assign
them any title unless verified.

BANNED phrasing — never use these or close variants:
"I came across", "came across your company", "caught my eye", "caught our attention",
"I've been following", "we discovered", "reaching out because we noticed",
"stumbled upon", "on my radar".
BANNED hype adjectives:
"thrilled", "excited to see", "impressed by your incredible", "amazing", "incredible",
"blown away". Write plain, genuine sentences a real person would send.

Subject lines must be plain and non-salesy, no clickbait, no emoji — e.g. "Your
application to Raed Ventures" or "Thanks for applying to Raed Ventures"."""

DRAFT_REGEN_USER_TEMPLATE = """The investment team has manually decided this lead is **{bucket}**.
This decision is FINAL — your only job is to write the email that matches this decision.
Do NOT re-evaluate the company. Do NOT switch the decision based on the context below.

Company: {company_name}
Contact (email recipient — not necessarily the founder; greet by name, no assumed title): {founder_names}
Background context (for tone only, not for re-deciding): {summary}

Rules per bucket — you MUST follow these exactly:
- YES: draft_type MUST be "meeting_request". Short, plain, warm email, MAX 50 WORDS,
  no bullet points. Open by thanking them for applying to Raed Ventures. You may add
  ONE concrete, factual sentence about what the company does (plain fact only, never
  flattery — omit it entirely if it isn't clear from the context). Ask if they'd
  like to book a short call and include this Calendly link:
  https://calendly.com/abdulrahman-raed/30min. Sign off as {associate_name}, Raed
  Ventures. Follow this shape:
    Hi {{name}},
    Thank you for applying to Raed Ventures. We had a look at {{company}} and we're
    interested to learn more about what you're building.
    Would you like to book a short call? {{calendly}}
    Best,
    {associate_name}, Raed Ventures
- MAYBE: draft_type, draft_subject, draft_body MUST all be null. Do not write an email.
- REJECT: draft_type MUST be "rejection". Short, honest, respectful email,
  MAX 70 WORDS, no bullet points. Open by thanking them for applying to Raed
  Ventures. State plainly that it isn't a fit for Raed right now, on a FIT basis (stage,
  sector, or business-model type). Keep the door open — wish them well, encourage
  them to reach out if things evolve. IMPORTANT: Never cite lack of information or
  insufficient data as a reason — always use a fit-based reason (stage, sector
  focus, model type). Sign off as {associate_name}, Raed Ventures.

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
Known contact(s) at the company: {founder_names}
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
