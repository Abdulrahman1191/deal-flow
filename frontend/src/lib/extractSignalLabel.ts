// A positive_signal / red_flag string from the model is usually a short
// topic phrase followed by an explanation ("Founder identity unverified —
// the only contact we have is unverified CRM data."). Splitting on the
// first strong delimiter recovers that topic phrase as a scannable label,
// while the detail (the full original string) stays available on demand.
//
// Only used as a fallback for plain-string signals (today's shape). Issue
// #200 lets the model supply its own `label` on {label, text} signals --
// ReasoningBox prefers that when present and only calls this for strings.
export interface ExtractedLabel {
  label: string;
  detail: string;
}

const DELIMITERS = [" — ", " – ", ": ", " (", ". "];
const MIN_LABEL_LEN = 8;
const MAX_LABEL_LEN = 70;
const FALLBACK_LEN = 60;
const TRAILING_PUNCTUATION = /[\s.,;:!?\-–—]+$/;

function stripTrailingPunctuation(text: string): string {
  return text.replace(TRAILING_PUNCTUATION, "");
}

// Earliest occurrence of any delimiter, not the earliest occurrence of each
// delimiter in turn — a ": " at position 40 loses to a " (" at position 12.
function firstDelimiterIndex(text: string): number {
  let earliest = -1;
  for (const delimiter of DELIMITERS) {
    const idx = text.indexOf(delimiter);
    if (idx !== -1 && (earliest === -1 || idx < earliest)) {
      earliest = idx;
    }
  }
  return earliest;
}

function truncateAtWordBoundary(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const slice = text.slice(0, maxLen);
  const lastSpace = slice.lastIndexOf(" ");
  const truncated = lastSpace > 0 ? slice.slice(0, lastSpace) : slice;
  return `${stripTrailingPunctuation(truncated)}…`;
}

export function extractSignalLabel(raw: string): ExtractedLabel {
  const delimiterIndex = firstDelimiterIndex(raw);
  if (delimiterIndex !== -1) {
    const prefix = raw.slice(0, delimiterIndex);
    if (prefix.length >= MIN_LABEL_LEN && prefix.length <= MAX_LABEL_LEN) {
      return { label: stripTrailingPunctuation(prefix), detail: raw };
    }
  }
  return { label: truncateAtWordBoundary(raw, FALLBACK_LEN), detail: raw };
}
