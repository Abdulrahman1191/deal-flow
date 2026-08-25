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

export interface LearnedReason {
  text: string;
  count: number;
  last_used_at: string;
  /** "personal" = the caller has used this reason before; "team" = a
   * fallback shown only when the caller doesn't have enough of their own. */
  source: "personal" | "team";
}

export interface MyReasons {
  rating_up: LearnedReason[];
  rating_down: LearnedReason[];
  bucket_yes: LearnedReason[];
  bucket_maybe: LearnedReason[];
  bucket_reject: LearnedReason[];
}

export const fetchMyReasons = () =>
  client.get<MyReasons>("/overrides/my-reasons").then((r) => r.data);
