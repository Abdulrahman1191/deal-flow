"""
Prebuilt rejection email templates, EN/AR (issue #232).

Plain Python constants -- no prompt, no model, no network call. Every
rejection sent through this module renders the same bytes for the same
(reasons, language, lead) input, which is the point: rejections are the bulk
of the board (267/346 open leads today) and the LLM's draft-to-draft
variation is not wanted here. Four templates, each keyed by a short reason
sentence/clause that differs; every other line is byte-identical across the
four templates within a language, by construction (they all format the same
*_BODY_TEMPLATE string).

Copy is held here, and only here, so wording can be changed by editing this
file alone (see the issue's appendix for the exact, partner-approved text,
including the copy-rule requirements: رائد with the hamza, the feminine
عليها, no invitation to re-apply in either language).
"""
from __future__ import annotations

from typing import Optional

from app.services.claude_agent import INTERNAL_ONLY_REASONS, UNQUAL_REASON_OPTIONS

CONFLICT = "CONFLICT"
MARKET_SIZE = "MARKET_SIZE"
TRACTION = "TRACTION"
MANDATE = "MANDATE"

# Precedence when several selected reasons map to different templates: one
# email, one reason, never two stacked sentences (issue #232).
TEMPLATE_PRECEDENCE = (CONFLICT, MARKET_SIZE, TRACTION, MANDATE)

# Canonical UNQUAL_REASON_OPTIONS label -> template it selects. Labels not
# present here (i.e. INTERNAL_ONLY_REASONS) never select a template -- see
# select_template_key below.
REASON_TEMPLATE_MAP: dict[str, str] = {
    "Conflict of interest": CONFLICT,
    "Market size": MARKET_SIZE,
    "Exit potential": MARKET_SIZE,
    "Lack of traction": TRACTION,
    "Business Model": TRACTION,
    "Out of our stage": MANDATE,
    "Out of our region": MANDATE,
    "Regulations and Legislation": MANDATE,
    "Technology and IP": MANDATE,
    "Other": MANDATE,
}

assert set(REASON_TEMPLATE_MAP) | INTERNAL_ONLY_REASONS == set(UNQUAL_REASON_OPTIONS)

EN_SUBJECT = "Raed Ventures — update on your application"
AR_SUBJECT = "رائد فنتشرز — تحديث بخصوص طلبكم"

# The reason is a lower-case CLAUSE completing "After reviewing your
# application, ..." -- not a standalone sentence. Each clause already
# includes its own closing full stop.
EN_REASON_CLAUSES: dict[str, str] = {
    CONFLICT: (
        "we are not able to take it further, as we have an existing commitment "
        "in your space that would put us in a conflict of interest."
    ),
    MARKET_SIZE: (
        "we don't believe the market you are addressing is large enough "
        "relative to the returns our fund needs to target."
    ),
    TRACTION: "we did not find enough evidence of traction to build conviction.",
    MANDATE: "we found that it falls outside our investment strategy at this time.",
}

# Arabic keeps its own two-sentence shape: "لقد اطّلعنا عليها باهتمام." then
# the reason as a full sentence -- not restructured to match the English
# clause form.
AR_REASON_SENTENCES: dict[str, str] = {
    CONFLICT: (
        "لا يمكننا المضي قدماً نظراً لوجود التزام قائم لدينا في المجال نفسه، "
        "ما يضعنا في موضع تعارض مصالح."
    ),
    MARKET_SIZE: (
        "لم نتمكّن في هذه المرحلة من الاقتناع أنّ حجم السوق الذي تستهدفونه كبير "
        "مقارنةً بالعوائد التي يستهدفها صندوقنا."
    ),
    TRACTION: (
        "لم نتمكّن من المضي قدماً في الوقت الحالي، إذ لم نجد مؤشرات نمو ملموسة "
        "كافية لبناء قناعة استثمارية في هذه المرحلة."
    ),
    MANDATE: "تقع فرصتكم خارج استراتيجية الاستثمار الخاصة بنا في الوقت الحالي.",
}

# {greeting} is computed by _greeting() rather than baked in as "Hi
# {first_name}," so a missing first_name can degrade to a usable "Hi there,"
# instead of "Hi ,". Neither language invites the founder back -- do not add
# "if things change"/"feel free to keep us posted" or any equivalent; the
# email closes the opportunity.
EN_BODY_TEMPLATE = """{greeting}

Thank you for sharing {company} with us, and for the time you put into the application.

After reviewing your application, {reason_clause}

This reflects where Raed is today rather than a judgement on what you are building.

We wish you the best of luck with it.

{partner_name}
Raed Ventures"""

AR_BODY_TEMPLATE = """{greeting}

شكراً لمشاركتكم {company} معنا، وعلى الوقت الذي خصصتموه لتقديم الطلب.

لقد اطّلعنا عليها باهتمام. {reason_sentence}

هذا القرار يعكس وضع رائد في الوقت الحالي أكثر مما يعكس حكماً على ما تبنونه.

نتمنى لكم كل التوفيق.

{partner_name}
رائد فنتشرز"""

_EN_FALLBACK_COMPANY = "your company"
_AR_FALLBACK_COMPANY = "مشروعكم"
_EN_FALLBACK_PARTNER = "The Raed Ventures Team"
_AR_FALLBACK_PARTNER = "فريق رائد فنتشرز"


def select_template_key(reasons: Optional[list[str]]) -> str:
    """Maps the partner's selected canonical reasons onto one of the four
    templates. INTERNAL_ONLY_REASONS never select a template -- if they are
    the only reasons selected (or nothing was selected at all), this falls
    back to MANDATE, same as a lead with no reasons picked yet."""
    shareable = [r for r in (reasons or []) if r not in INTERNAL_ONLY_REASONS]
    selected_keys = {REASON_TEMPLATE_MAP[r] for r in shareable if r in REASON_TEMPLATE_MAP}
    for key in TEMPLATE_PRECEDENCE:
        if key in selected_keys:
            return key
    return MANDATE


def first_name_from_founders(founder_names: Optional[list[str]]) -> Optional[str]:
    """First token of the first listed founder's name, e.g. ["Jane Doe"] ->
    "Jane". None (not "") when there's nothing usable, so callers can tell
    "degrade the greeting" apart from "empty string was the actual name"."""
    if not founder_names:
        return None
    full = (founder_names[0] or "").strip()
    return full.split()[0] if full else None


def render_rejection_email(
    *,
    reasons: Optional[list[str]],
    language: str,
    first_name: Optional[str],
    company: Optional[str],
    partner_name: Optional[str] = None,
) -> dict[str, str]:
    """Pure function: same inputs always render the same bytes. Makes no
    network call and no LLM call -- it never touches claude_agent's
    _chat_completion choke point."""
    template_key = select_template_key(reasons)
    lang = "ar" if language == "ar" else "en"

    if lang == "ar":
        greeting = f"مرحباً {first_name}،" if first_name else "مرحباً،"
        body = AR_BODY_TEMPLATE.format(
            greeting=greeting,
            company=company or _AR_FALLBACK_COMPANY,
            reason_sentence=AR_REASON_SENTENCES[template_key],
            partner_name=partner_name or _AR_FALLBACK_PARTNER,
        )
        subject = AR_SUBJECT
    else:
        greeting = f"Hi {first_name}," if first_name else "Hi there,"
        body = EN_BODY_TEMPLATE.format(
            greeting=greeting,
            company=company or _EN_FALLBACK_COMPANY,
            reason_clause=EN_REASON_CLAUSES[template_key],
            partner_name=partner_name or _EN_FALLBACK_PARTNER,
        )
        subject = EN_SUBJECT

    return {"template_key": template_key, "language": lang, "subject": subject, "body": body}
