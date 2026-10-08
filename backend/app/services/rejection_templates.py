"""
Prebuilt rejection email templates (issue #232) -- plain Python constants, no
prompt and no model. Four templates (CONFLICT / MARKET_SIZE / TRACTION /
MANDATE) x two languages (en/ar); the surrounding copy is byte-identical
across all four templates within a language, only the one reason
clause/sentence differs. Rendering is a pure function with no network call
and no LLM call -- same lead + same reasons + same language always produce
the same bytes.

This module deliberately has NO import of claude_agent's OpenAI client code
path -- it only pulls in the two reason-label constants
(UNQUAL_REASON_OPTIONS, INTERNAL_ONLY_REASONS) that are the single source of
truth for the canonical Copper labels, so this mapping table can't silently
drift out of sync with them.
"""
from __future__ import annotations

from app.services.claude_agent import INTERNAL_ONLY_REASONS, UNQUAL_REASON_OPTIONS

# Precedence when several selected reasons map to different templates (issue
# #232): one email, one reason, never two stacked sentences.
TEMPLATE_KEYS: tuple[str, ...] = ("CONFLICT", "MARKET_SIZE", "TRACTION", "MANDATE")

# Maps every *shareable* UNQUAL_REASON_OPTIONS label onto the template that
# covers it. INTERNAL_ONLY_REASONS are deliberately absent here -- they
# never select a template, see select_template().
REASON_TO_TEMPLATE: dict[str, str] = {
    "Conflict of interest": "CONFLICT",
    "Market size": "MARKET_SIZE",
    "Exit potential": "MARKET_SIZE",
    "Lack of traction": "TRACTION",
    "Business Model": "TRACTION",
    "Out of our stage": "MANDATE",
    "Out of our region": "MANDATE",
    "Regulations and Legislation": "MANDATE",
    "Technology and IP": "MANDATE",
    "Other": "MANDATE",
}

# Every UNQUAL_REASON_OPTIONS label must be either mapped to a template above
# or carved out as internal-only -- guards this table against drifting out of
# sync with claude_agent.UNQUAL_REASON_OPTIONS as reasons are added/renamed.
assert set(REASON_TO_TEMPLATE) | set(INTERNAL_ONLY_REASONS) == set(UNQUAL_REASON_OPTIONS)

_EN_SUBJECT = "Raed Ventures — update on your application"

# The reason is a lower-case clause completing the "After reviewing your
# application," sentence, not a sentence of its own -- each clause below
# carries its own full stop.
_EN_BODY_TEMPLATE = """{greeting_line}

Thank you for sharing {company} with us, and for the time you put into the application.

After reviewing your application, {reason}

This reflects where Raed Ventures is today rather than a judgement on what you are building.

We wish you the best of luck with it.

{partner_name}
Raed Ventures"""

_EN_REASONS: dict[str, str] = {
    "CONFLICT": (
        "we are not able to take it further, as we have an existing commitment in your space "
        "that would put us in a conflict of interest."
    ),
    "MARKET_SIZE": (
        "we don't believe the market you are addressing is large enough relative to the "
        "returns our fund needs to target."
    ),
    "TRACTION": "we did not find enough evidence of traction to build conviction.",
    "MANDATE": "we found that it falls outside our investment strategy at this time.",
}

# Arabic keeps its own two-sentence shape (لقد اطلعنا على الفرصة. then the
# reason as a full sentence) -- do not restructure it to match the English
# clause form. Copy every Arabic string byte for byte, including diacritics
# (e.g. حكمًا, تمكّن) -- do not normalise or "tidy" the Arabic.
_AR_SUBJECT = "رائد فنتشرز — تحديث بخصوص طلبكم"

_AR_BODY_TEMPLATE = """{greeting_line}

شكراً لمشاركتكم {company} معنا، وعلى الوقت الذي خصصتموه لتقديم الطلب.

لقد اطلعنا على الفرصة. {reason}

هذا القرار يعكس وضع رائد فنتشرز في الوقت الحالي أكثر مما يعكس حكمًا على ما تتمنونه.

نتمنى لكم التوفيق.

{partner_name}
رائد فنتشرز"""

_AR_REASONS: dict[str, str] = {
    "CONFLICT": (
        "لا يمكننا المضي قدماً نظراً لوجود التزام قائم لدينا في المجال نفسه، ما يضعنا في موضع "
        "تعارض مصالح."
    ),
    "MARKET_SIZE": (
        "لم نتمكّن في هذه المرحلة من الاقتناع أنّ حجم السوق الذي تستهدفونه كبير مقارنةً بالعوائد "
        "التي يستهدفها صندوقنا."
    ),
    "TRACTION": (
        "لم نتمكّن من المضي قدماً في الوقت الحالي، إذ لم نجد مؤشرات نمو ملموسة كافية لبناء قناعة "
        "استثمارية في هذه المرحلة."
    ),
    "MANDATE": "تقع فرصتكم خارج استراتيجية الاستثمار الخاصة بنا في الوقت الحالي.",
}


def select_template(reasons: list[str] | None) -> str:
    """Picks one of the four templates from a partner's selected
    UNQUAL_REASON_OPTIONS labels (issue #232).

    INTERNAL_ONLY_REASONS never select a template. If they are the only
    reasons selected -- or nothing was selected at all -- this falls back to
    MANDATE, the same "doesn't fit our strategy right now" wording used for
    the generic case. When several selected reasons map to different
    templates, precedence is CONFLICT > MARKET_SIZE > TRACTION > MANDATE: one
    email, one reason, never two stacked sentences."""
    candidates = {REASON_TO_TEMPLATE[r] for r in (reasons or []) if r in REASON_TO_TEMPLATE}
    for template in TEMPLATE_KEYS:
        if template in candidates:
            return template
    return "MANDATE"


def _greeting_line(first_name: str | None, language: str) -> str:
    """A missing first_name degrades to a usable generic greeting rather
    than rendering "Hi ," / "مرحباً ،"."""
    name = (first_name or "").strip()
    if language == "ar":
        return f"مرحباً {name}،" if name else "مرحباً،"
    return f"Hi {name}," if name else "Hi there,"


def render_rejection_email(
    *,
    first_name: str | None,
    company: str | None,
    partner_name: str | None,
    reasons: list[str] | None,
    language: str,
) -> dict[str, str]:
    """Renders a rejection email from the prebuilt templates -- pure
    function, no network call and no LLM call: the same lead, reasons, and
    language always produce the same bytes (issue #232).

    `language` must be "en" or "ar" -- callers resolve the default via
    claude_agent.detect_applicant_language and pass the partner's EN/AR
    toggle override straight through."""
    if language not in ("en", "ar"):
        raise ValueError(f"language must be 'en' or 'ar', got {language!r}")

    template = select_template(reasons)
    greeting_line = _greeting_line(first_name, language)
    company_name = (company or "").strip()
    partner = (partner_name or "").strip()

    if language == "ar":
        body = _AR_BODY_TEMPLATE.format(
            greeting_line=greeting_line,
            company=company_name,
            reason=_AR_REASONS[template],
            partner_name=partner,
        )
        subject = _AR_SUBJECT
    else:
        body = _EN_BODY_TEMPLATE.format(
            greeting_line=greeting_line,
            company=company_name,
            reason=_EN_REASONS[template],
            partner_name=partner,
        )
        subject = _EN_SUBJECT

    return {"subject": subject, "body": body, "template": template, "language": language}
