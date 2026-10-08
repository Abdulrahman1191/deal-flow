import client from "./client";
import type { Assessment } from "../types/lead";

export interface SendQueueItem {
  lead_id: string;
  assessment_id: string;
  company_name: string;
  draft_type: "rejection" | "meeting_request";
  recipient_email: string;
  draft_subject: string | null;
  draft_body: string | null;
  approved_at: string;
}

export const fetchAssessment = (leadId: string) =>
  client.get<Assessment>(`/assessments/${leadId}`).then((r) => r.data);

export const fetchSendQueue = () =>
  client.get<SendQueueItem[]>("/assessments/send-queue").then((r) => r.data);

export const approveAssessment = (leadId: string) =>
  client.post(`/assessments/${leadId}/approve`).then((r) => r.data);

export const markSent = (leadId: string) =>
  client.post(`/assessments/${leadId}/mark-sent`).then((r) => r.data);

// Actually sends the drafted email (SMTP via SES/SendGrid) and finalizes the
// lead (Copper convert/archive). Returns 503 if email isn't configured yet.
export const sendEmail = (leadId: string) =>
  client.post(`/assessments/${leadId}/send`).then((r) => r.data);

export const updateDraft = (leadId: string, data: { draft_subject?: string; draft_body?: string }) =>
  client.patch<Assessment>(`/assessments/${leadId}/draft`, data).then((r) => r.data);

export interface OverrideReason {
  reason_tags?: string[];
  reason?: string;
}

export const overrideBucket = (
  leadId: string,
  bucket: string,
  reasonData?: OverrideReason,
) =>
  client
    .post<Assessment>(`/assessments/${leadId}/override`, { bucket, ...reasonData })
    .then((r) => r.data);

export const rateAssessment = (
  leadId: string,
  rating: "up" | "down",
  reasonData?: OverrideReason,
) =>
  client
    .post<Assessment>(`/assessments/${leadId}/rate`, { rating, ...reasonData })
    .then((r) => r.data);

export const reassess = (leadId: string) =>
  client.post(`/assessments/${leadId}/reassess`).then((r) => r.data);

export const regenerateDraft = (leadId: string, reasons?: string[]) =>
  client
    .post<Assessment>(`/assessments/${leadId}/regenerate-draft`, reasons ? { reasons } : undefined)
    .then((r) => r.data);

// Zero-LLM counterpart to regenerateDraft (issue #232) -- renders one of the
// four prebuilt rejection templates instead of calling the model. `language`
// omitted lets the backend default to claude_agent.detect_applicant_language;
// pass "en"/"ar" to reflect the partner's toggle override.
export interface RejectionTemplateResult extends Assessment {
  rejection_template_key: "CONFLICT" | "MARKET_SIZE" | "TRACTION" | "MANDATE";
  rejection_template_language: "en" | "ar";
}

export const renderRejectionTemplate = (
  leadId: string,
  data: { reasons?: string[]; language?: "en" | "ar" },
) =>
  client
    .post<RejectionTemplateResult>(`/assessments/${leadId}/rejection-template`, data)
    .then((r) => r.data);
