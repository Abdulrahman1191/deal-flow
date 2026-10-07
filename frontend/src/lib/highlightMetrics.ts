// Bolds numeric spans (currency amounts, percentages, counts-with-a-unit)
// inside the expanded detail text of a signal/flag (issue #221) -- the
// scannable label already made the POINT easy to find; this makes the hard
// NUMBER inside the full-sentence detail easy to find too.
//
// Returns an HTML string for dangerouslySetInnerHTML, so the input is
// HTML-escaped FIRST and the <strong> wrapping happens on the escaped text --
// a signal/flag sentence containing "<" or "&" can never inject markup.

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

const CURRENCY_CODES = "SAR|AED|USD|EGP|GBP|EUR|KWD|QAR|BHD|OMR|JOD|MAD|TND";
// (?<![\w.-]) stops the number matching the trailing digits of a token like
// "GPT-4" or "v2.1" -- a metric's number always starts at a real boundary.
const NUM = "(?<![\\w.-])\\d[\\d,]*(?:\\.\\d+)?";
// The multiplier letter is only consumed together with its (optional)
// leading space -- `\s?[KMB]?` would otherwise greedily eat a bare number's
// trailing space even with no K/M/B present (e.g. "$5 from" -> "$5 ").
const AMOUNT_TAIL = `${NUM}(?:\\s?[KMB])?\\+?`;

// Alternatives are tried in order at each position:
//   1. symbol-prefixed currency ($220K, £1M+)
//   2. code-prefixed currency (SAR 1.5M+, AED 500K)
//   3. percentage (18%)
//   4. a count followed by a short lowercase unit phrase (550 vendors,
//      3,000+ registered users, 8 paid stores)
const METRIC_RE = new RegExp(
  [
    `[$£€]\\s?${AMOUNT_TAIL}`,
    `\\b(?:${CURRENCY_CODES})\\s?${AMOUNT_TAIL}`,
    `${NUM}%`,
    `${NUM}\\+?(?:\\s+[a-z][a-z'-]*){1,3}`,
  ].join("|"),
  "g",
);

export function highlightMetrics(text: string): string {
  const escaped = escapeHtml(text ?? "");
  return escaped.replace(METRIC_RE, (match) => `<strong>${match}</strong>`);
}
