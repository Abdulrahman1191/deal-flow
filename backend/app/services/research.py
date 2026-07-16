"""
Web research using Tavily — LLM-optimised search API.
Replaces the Cowork browser agent. No authenticated sessions required;
results cover public web including Crunchbase, LinkedIn public pages, news.
"""
from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse

from tavily import TavilyClient

from app.config import settings

_client = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=settings.tavily_api_key)
    return _client


def _is_public_host(hostname: str) -> bool:
    """True only if every address `hostname` resolves to is a public,
    routable IP — no RFC1918/loopback/link-local/reserved/multicast range.
    Blocks the SSRF class described in SECURITY_AUDIT.md F5 (e.g. a lead's
    `website` pointed at the cloud metadata endpoint or an internal service)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    return _is_public_host(parsed.hostname)


def scrape_linkedin_from_website(website: str | None) -> str | None:
    """
    Fetch the company's own website and extract a linkedin.com/company/* URL
    from the HTML (footer / nav / social icons). This is the most reliable
    source — companies link to their own LinkedIn from their own site.
    """
    if not website:
        return None
    url = website if website.startswith("http") else f"https://{website}"
    if not _is_safe_url(url):
        return None
    try:
        import httpx, re as _re

        def _block_unsafe_redirect(request: "httpx.Request") -> None:
            # Also guards every hop of a redirect chain, not just the initial URL.
            if not _is_safe_url(str(request.url)):
                raise ValueError(f"blocked SSRF-unsafe target: {request.url}")

        with httpx.Client(
            timeout=8,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RaedBot/1.0)"},
            event_hooks={"request": [_block_unsafe_redirect]},
        ) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return None
        m = _re.search(
            r'https?://(?:www\.)?linkedin\.com/(?:company|school)/[A-Za-z0-9_\-\.%]+',
            r.text,
        )
        if m:
            return m.group(0).split("?")[0].rstrip("/")
    except Exception:
        return None
    return None


def find_linkedin_via_llm_search(
    company_name: str,
    website: str = "",
    founder_names: list | None = None,
    description: str = "",
    region: str = "",
) -> str | None:
    """
    Fallback when website-scrape fails. Runs a broad Tavily search using the
    full lead context, extracts every linkedin.com/company/* URL we can find
    across result content + titles + URLs, then asks DeepSeek to pick the
    one that actually matches this company (or return None if uncertain).
    """
    if not company_name:
        return None

    import re as _re
    founders = ", ".join(founder_names or [])
    desc_short = (description or "")[:200]
    query_parts = [company_name, website, founders, desc_short, region, "company"]
    query = " ".join(p for p in query_parts if p).strip()

    try:
        r = _get_client().search(query, max_results=10)
    except Exception as e:
        print(f"[linkedin-llm] tavily search failed: {e!r}")
        return None

    pattern = _re.compile(
        r'https?://(?:www\.)?linkedin\.com/company/[A-Za-z0-9_\-\.%]+',
        _re.IGNORECASE,
    )
    candidates = set()
    for item in r.get("results", []):
        haystack = (
            (item.get("content") or "")
            + " " + (item.get("url") or "")
            + " " + (item.get("title") or "")
        )
        for m in pattern.findall(haystack):
            candidates.add(m.split("?")[0].rstrip("/"))

    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates))

    # Ambiguous — let the LLM pick.
    from app.services.claude_agent import pick_linkedin_url
    return pick_linkedin_url(
        company_name=company_name,
        founder_names=founder_names or [],
        description=description,
        region=region,
        candidates=sorted(candidates),
    )


# Legacy alias retained for callers that haven't moved yet.
def find_company_linkedin(company_name: str, website: str = "") -> str | None:
    return find_linkedin_via_llm_search(company_name=company_name, website=website)


def _deck_keywords(deck_text: str | None) -> str:
    """Extract a handful of distinguishing technical terms from the pitch deck
    to feed a targeted Tavily query. Heuristic only — looks for noun-phrase
    candidates and filters obvious filler."""
    if not deck_text:
        return ""
    import re as _re
    from collections import Counter

    # Strip pure-numeric tokens, very short words, very common stop words.
    stop = {
        "the", "and", "for", "with", "our", "are", "this", "that", "from",
        "have", "has", "will", "can", "their", "they", "your", "you", "all",
        "company", "team", "market", "product", "business", "founder", "founders",
        "what", "when", "where", "why", "how", "who", "which", "than", "more",
        "raed", "ventures", "page", "slide", "deck", "investor", "pitch",
    }
    tokens = [t.lower() for t in _re.findall(r"[A-Za-z]{4,20}", deck_text)]
    counter = Counter(t for t in tokens if t not in stop)
    # Top 8 distinctive terms
    top = [w for w, _ in counter.most_common(15)][:8]
    return " ".join(top)


def research_company(lead: dict) -> dict:
    company = lead.get("company_name", "")
    website = lead.get("website", "")
    founders = ", ".join(lead.get("founder_names") or [])
    region = lead.get("region", "") or ""
    description = (lead.get("description") or "")[:200]
    deck_text = lead.get("pitch_deck_text") or ""

    queries = [
        # 1. Funding + stage + round history
        f"{company} startup funding seed Series A round investors valuation",
        # 2. Founder DISCOVERY — who actually founded the company. Deliberately
        #    does NOT assert the CRM contact is a founder: the contact on an
        #    inbound lead is often an employee or advisor, and seeding their
        #    name into a "founder background" query used to make Tavily return
        #    a founder-shaped profile around the wrong person.
        f"{company} founder co-founder CEO \"founded by\" LinkedIn",
        # 3. Tech / IP / patents / engineering depth
        f"{company} technology patents IP proprietary algorithm research",
        # 4. MENA market presence — operations, offices, target customers
        (f"{company} {region} Saudi Arabia UAE MENA market customers operations"
         if region else f"{company} MENA Middle East Saudi UAE market"),
        # 5. Competition / who else does this — calibration signal
        f"{company} competitors alternatives similar startups industry landscape",
        # 6. Recent news + press — traction, partnerships, announcements
        f"{company} news announcement partnership launch 2025 2026",
    ]

    # Contact-identity probe — neutral phrasing, no "founder" assertion. Surfaces
    # who the CRM contact actually is (their real title/role at the company) so
    # the assessor can verify or refute the founder assumption.
    if founders:
        queries.append(f"{founders} {company} LinkedIn role title position")

    # Pitch-deck-driven query: distinguishing technical terms from the deck
    # combined with the company name, so Tavily returns matches for the
    # *specific* technology approach the founders claim, not a generic synonym.
    deck_kw = _deck_keywords(deck_text)
    if deck_kw:
        queries.append(f"{company} {deck_kw} startup technology")

    results = {}
    for q in queries:
        try:
            r = _get_client().search(q, max_results=5, include_answer=True)
            results[q] = r
        except Exception as e:
            results[q] = {"error": str(e)}

    return results


def research_briefing_topics(date_str: str) -> dict:
    queries = [
        "MENA deep tech startup investments funding announcements this week",
        "Sequoia a16z Khosla Founders Fund new investments announcements this week",
        "Saudi Vision 2030 tech regulatory updates investment",
        "KAUST MBZUAI research breakthroughs deep tech",
        "emerging deep tech business models venture capital 2025",
        "deep tech patent filings IP news semiconductor biotech",
    ]

    results = {}
    for q in queries:
        try:
            r = _get_client().search(q, max_results=5, include_answer=True)
            results[q] = r
        except Exception as e:
            results[q] = {"error": str(e)}

    return results
