"""
Issue #232: four prebuilt rejection templates (EN/AR) the partner picks with
one click, instead of a fresh DeepSeek-generated rejection draft every time.

Plain Python constants and pure functions -- no prompt, no model, nothing in
this module ever calls `claude_agent._chat_completion`. Rendering is
deterministic: the same lead + the same selected reasons always produce the
same bytes, which is what makes a REJECT draft unit-testable without mocking
an LLM, and what makes a bulk send of N leads cost zero LLM calls regardless
of batch size.

The surrounding copy is byte-identical across all four templates within a
language -- only the one `{reason_sentence}` sentence differs -- so wording
changes are a one-line edit to the constants below, never a prompt rewrite.
"""
from __future__ import annotations
from typing import Any, Optional

from app.services.claude_agent import INTERNAL_ONLY_REASONS, detect_applicant_language

# The four fixed templates, in the precedence order used when more than one
# selected reason maps to a different template -- one email, one reason,
# never two stacked sentences.
TEMPLATE_CONFLICT = "CONFLICT"
TEMPLATE_MARKET_SIZE = "MARKET_SIZE"
TEMPLATE_TRACTION = "TRACTION"
TEMPLATE_MANDATE = "MANDATE"

TEMPLATE_PRECEDENCE = (TEMPLATE_CONFLICT, TEMPLATE_MARKET_SIZE, TEMPLATE_TRACTION, TEMPLATE_MANDATE)

# Maps every canonical claude_agent.UNQUAL_REASON_OPTIONS label that is
# shareable with the founder (i.e. not in INTERNAL_ONLY_REASONS) onto the
# template it selects, per the issue's table. A label absent from this map
# (including every INTERNAL_ONLY_REASONS label) never selects a template on
# its own -- MANDATE is the fallback when nothing else maps.
REASON_TO_TEMPLATE: dict[str, str] = {
    "Conflict of interest": TEMPLATE_CONFLICT,
    "Market size": TEMPLATE_MARKET_SIZE,
    "Exit potential": TEMPLATE_MARKET_SIZE,
    "Lack of traction": TEMPLATE_TRACTION,
    "Business Model": TEMPLATE_TRACTION,
    "Out of our stage": TEMPLATE_MANDATE,
    "Out of our region": TEMPLATE_MANDATE,
    "Regulations and Legislation": TEMPLATE_MANDATE,
    "Technology and IP": TEMPLATE_MANDATE,
    "Other": TEMPLATE_MANDATE,
}

_EN_SUBJECT = "Raed Ventures — update on your application"
_AR_SUBJECT = "رايد فنتشرز — تحديث بخصوص طلبكم"

_EN_BODY = """Hi {first_name},

Thank you for sharing {company} with us, and for the time you put into the application.

We reviewed it carefully. {reason_sentence}

This reflects where Raed is today rather than a judgement on what you are building, and it is not a permanent no — if things change on either side, we would be glad to hear from you again.

Wishing you the best with it.

{partner_name}
Raed Ventures"""

_AR_BODY = """مرحباً {first_name}،

شكراً لمشاركتكم {company} معنا، وعلى الوقت الذي خصصتموه لتقديم الطلب.

لقد اطّلعنا عليه باهتمام. {reason_sentence}

هذا القرار يعكس وضع رايد في الوقت الحالي أكثر مما يعكس حكماً على ما تبنونه، وهو ليس رفضاً نهائياً — وإن تغيّرت المعطيات لدى أيٍّ من الطرفين، فيسعدنا أن نسمع منكم مجدداً.

نتمنى لكم كل التوفيق.

{partner_name}
رايد فنتشرز"""

_EN_REASON_SENTENCES: dict[str, str] = {
    TEMPLATE_CONFLICT: (
        "We are not able to take it further, as we have an existing commitment in your "
        "space that would put us in a conflict of interest."
    ),
    TEMPLATE_MARKET_SIZE: (
        "At this stage we are not able to get comfortable with the size of the market "
        "you are addressing relative to the returns our fund needs to target."
    ),
    TEMPLATE_TRACTION: (
        "We are not able to move forward yet, as we did not find enough evidence of "
        "traction to build conviction at this point."
    ),
    TEMPLATE_MANDATE: (
        "It falls outside the mandate we are investing against at the moment, so we "
        "will not be taking it further."
    ),
}

_AR_REASON_SENTENCES: dict[str, str] = {
    TEMPLATE_CONFLICT: "لا يمكننا المضي قدماً نظراً لوجود التزام قائم لدينا في المجال نفسه، ما يضعنا في موضع تعارض مصالح.",
    TEMPLATE_MARKET_SIZE: "لم نتمكّن في هذه المرحلة من الاطمئنان إلى حجم السوق الذي تستهدفونه مقارنةً بالعوائد التي يستهدفها صندوقنا.",
    TEMPLATE_TRACTION: "لم نتمكّن من المضي قدماً في الوقت الحالي، إذ لم نجد مؤشرات نمو ملموسة كافية لبناء قناعة استثمارية في هذه المرحلة.",
    TEMPLATE_MANDATE: "يقع خارج نطاق التفويض الاستثماري الذي نعمل وفقه حالياً، لذا لن نتمكّن من المضي قدماً.",
}

_SUBJECTS = {"en": _EN_SUBJECT, "ar": _AR_SUBJECT}
_BODIES = {"en": _EN_BODY, "ar": _AR_BODY}
_REASON_SENTENCES = {"en": _EN_REASON_SENTENCES, "ar": _AR_REASON_SENTENCES}

# Fallback greeting name/company when the lead has neither (issue #232: "a
# missing first_name degrades to a usable greeting rather than rendering
# 'Hi ,'"). Substituted into the exact same {first_name}/{company}
# placeholders as a real value -- the surrounding copy never changes.
_FALLBACK_FIRST_NAME = {"en": "there", "ar": "بكم"}
_FALLBACK_COMPANY = {"en": "your company", "ar": "شركتكم"}

DEFAULT_PARTNER_NAME = "Raed Ventures"


def select_template(reasons: Optional[list[str]]) -> str:
    """Picks one of the four template keys for the given canonical
    UNQUAL_REASON_OPTIONS labels, per the issue's precedence
    (CONFLICT > MARKET_SIZE > TRACTION > MANDATE).

    INTERNAL_ONLY_REASONS (judgements about the people, never the business)
    are dropped before mapping -- they must never select a template or reach
    a founder. When nothing shareable was selected (no reasons at all, or
    only internal-only ones), falls back to MANDATE: a safe, generic,
    always-true closing line.
    """
    shareable = {r for r in (reasons or []) if r not in INTERNAL_ONLY_REASONS}
    mapped = {REASON_TO_TEMPLATE[r] for r in shareable if r in REASON_TO_TEMPLATE}
    for key in TEMPLATE_PRECEDENCE:
        if key in mapped:
            return key
    return TEMPLATE_MANDATE


def _first_name(lead_data: dict) -> Optional[str]:
    for name in lead_data.get("founder_names") or []:
        if name and str(name).strip():
            first = str(name).strip().split()[0]
            if first:
                return first
    return None


def render_rejection_email(
    *,
    lead_data: dict[str, Any],
    reasons: Optional[list[str]] = None,
    language: Optional[str] = None,
    partner_name: Optional[str] = None,
) -> dict[str, str]:
    """Renders one of the four fixed rejection templates. Pure string
    formatting -- no network call, no LLM call, so the same (lead_data,
    reasons, language) always produces the same bytes.

    `language` overrides the deterministic `detect_applicant_language`
    signal (issue #92/#168) when given ("en"/"ar"); anything else (None, or
    an unrecognised value) falls back to detection.
    """
    lang = language if language in ("en", "ar") else detect_applicant_language(lead_data)
    template_key = select_template(reasons)

    first_name = _first_name(lead_data) or _FALLBACK_FIRST_NAME[lang]
    company = (lead_data.get("company_name") or "").strip() or _FALLBACK_COMPANY[lang]
    signer = partner_name or DEFAULT_PARTNER_NAME
    reason_sentence = _REASON_SENTENCES[lang][template_key]

    body = _BODIES[lang].format(
        first_name=first_name, company=company, reason_sentence=reason_sentence, partner_name=signer,
    )
    return {
        "template": template_key,
        "language": lang,
        "subject": _SUBJECTS[lang],
        "body": body,
    }
