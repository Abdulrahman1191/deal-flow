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
  status: string;
  created_at: string;
  updated_at: string;
  applied_at: string | null;
  assessment?: Assessment;
}

export interface Assessment {
  id: string;
  lead_id: string;
  bucket: "YES" | "MAYBE" | "REJECT";
  confidence_score: number;
  summary: string | null;
  positive_signals: string[] | null;
  red_flags: string[] | null;
  data_gaps: string[] | null;
  scoring_breakdown: Record<string, { score: number; reasoning: string }> | null;
  draft_subject: string | null;
  draft_body: string | null;
  draft_type: "rejection" | "meeting_request" | null;
  research_sources: string[] | null;
  user_override: string | null;
  user_override_at: string | null;
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
