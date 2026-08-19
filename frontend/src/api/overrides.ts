import client from "./client";

export interface OverrideItem {
  id: string;
  lead_id: string;
  assessment_id: string;
  ai_bucket: string;
  ai_confidence: number | null;
  ai_summary: string | null;
  human_bucket: string;
  trigger: string;
  disagreement: boolean;
  has_research: boolean;
  has_deck: boolean;
  created_at: string;
}

export const fetchOverrides = (params?: { only_disagreements?: boolean; limit?: number }) =>
  client.get<OverrideItem[]>("/overrides", { params }).then((r) => r.data);

export interface WeeklyAgreement {
  week_start: string;
  total: number;
  agreements: number;
  agreement_rate: number;
}

export interface PartnerProfile {
  acted_by_email: string;
  total: number;
  agreement_rate: number;
  confirm_rate: number;
  correction_rate: number;
  rate_down_rate: number;
  articulation_rate: number;
}

export interface CalibrationStats {
  total_rows: number;
  agreements: number;
  disagreements: number;
  agreement_rate: number | null;
  agreement_over_time: WeeklyAgreement[];
  partner_profiles: PartnerProfile[];
  disagreement_pairs: Record<string, number>;
  excluded_test_accounts: string[];
}

export const fetchCalibrationStats = () =>
  client.get<CalibrationStats>("/overrides/calibration").then((r) => r.data);
