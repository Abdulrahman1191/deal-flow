/**
 * Fixed Copper "Unqualification Reasons" MultiSelect options (custom field
 * 244358). Mirrors `UNQUAL_REASON_OPTIONS` in
 * backend/app/services/claude_agent.py — keep the two in sync.
 */
export interface UnqualificationReason {
  label: string;
  id: number;
}

export const UNQUALIFICATION_REASONS: UnqualificationReason[] = [
  { label: "Founder(s)", id: 1529401 },
  { label: "Out of our stage", id: 367300 },
  { label: "Out of our region", id: 367301 },
  { label: "Lack of traction", id: 367302 },
  { label: "Dedication and focus", id: 367303 },
  { label: "Ownership structure", id: 367304 },
  { label: "Market size", id: 367305 },
  { label: "Business Model", id: 367306 },
  { label: "Regulations and Legislation", id: 367307 },
  { label: "Technology and IP", id: 367308 },
  { label: "Exit potential", id: 367309 },
  { label: "Conflict of interest", id: 367310 },
  { label: "Other", id: 367311 },
];
