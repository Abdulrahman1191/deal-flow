export interface Lead {
  id: string;
  copper_id: string | null;
  company_name: string;
  website: string | null;
  description: string | null;
  stage: string | null;
  region: string | null;
  founder_names: string[] | null;
  linkedin_urls: string[] | null;
  company_linkedin_url: string | null;
  pitch_deck_filename: string | null;
  pitch_deck_ingested_at: string | null;
  pitch_deck_drive_id: string | null;
  prior_contact: boolean | null;
  prior_contact_count: number | null;
  prior_contact_last_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  applied_at: string | null;
  assessment?: Assessment;
}

// A positive_signals/red_flags entry is either a plain string (today's
// shape -- ~1,000 existing cards) or an object carrying a model-chosen short
// `label` alongside the full `text` (issue #200). Readers must accept both
// shapes forever; see ReasoningBox's extractSignalLabel.
export type Signal = string | { label?: string | null; text: string };

export interface Assessment {
  id: string;
  lead_id: string;
  bucket: "YES" | "MAYBE" | "REJECT";
  confidence_score: number;
  summary: string | null;
  positive_signals: Signal[] | null;
  red_flags: Signal[] | null;
  // Evidenced hard metrics (issue #221), e.g. "$220K GMV", "550 vendors" -- at
  // most 4, in the model's order. Always an array (never null) -- an older
  // card with no traction field and a freshly-assessed card with no evidence
  // both come back as [], so the card shows no row either way.
  traction: string[];
  data_gaps: string[] | null;
  scoring_breakdown: Record<string, { score: number; reasoning: string }> | null;
  draft_subject: string | null;
  draft_body: string | null;
  draft_type: "rejection" | "meeting_request" | null;
  // Bucket the current draft_subject/draft_body/draft_type were actually
  // written for (issue #150). Compared against the effective bucket
  // (user_override ?? bucket) to detect a stale draft — see EmailModal.
  draft_bucket: "YES" | "MAYBE" | "REJECT" | null;
  research_sources: string[] | null;
  assessed_without_deck: boolean;
  user_override: string | null;
  user_override_at: string | null;
  // Partner-selected pass reasons for the current REJECT draft (issue #223)
  // -- canonical UNQUAL_REASON_OPTIONS labels, capped at 3. Null for a YES
  // lead or a REJECT that's never had reasons chosen. Lets EmailModal
  // preselect the same chips when the modal is reopened.
  rejection_reasons: string[] | null;
  user_rating: "up" | "down" | null;
  user_rating_at: string | null;
  approved_at: string | null;
  sent_at: string | null;
  created_at: string;
}

export interface PaginatedLeads {
  total: number;
  page: number;
  page_size: number;
  items: Lead[];
}
