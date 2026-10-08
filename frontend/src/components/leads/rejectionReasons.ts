// Mirrors backend `UNQUAL_REASON_OPTIONS` (app/services/claude_agent.py) --
// the fixed Copper "Unqualification Reasons" labels /regenerate-draft
// validates against (issue #223/#224). There's no endpoint that serves this
// list today, so keep the two in sync by hand if Copper's options change.
export const UNQUAL_REASON_LABELS: string[] = [
  "Founder(s)",
  "Out of our stage",
  "Out of our region",
  "Lack of traction",
  "Dedication and focus",
  "Ownership structure",
  "Market size",
  "Business Model",
  "Regulations and Legislation",
  "Technology and IP",
  "Exit potential",
  "Conflict of interest",
  "Other",
];

// Judgements about the people, never the business -- regenerate_draft
// silently drops these before they ever reach the founder's inbox, even
// when selected. Still recorded verbatim and still drives Copper.
export const INTERNAL_ONLY_REASONS: ReadonlySet<string> = new Set([
  "Founder(s)",
  "Dedication and focus",
  "Ownership structure",
]);

// Mirrors backend MAX_REJECTION_REASONS (app/schemas/assessment.py).
export const MAX_REJECTION_REASONS = 3;
