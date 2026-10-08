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

// `reasons` (issue #223/#224) are canonical UNQUAL_REASON_OPTIONS labels the
// partner picked to explain a REJECT -- omitted entirely (not sent as an
// empty array) when there's nothing selected, matching the optional body
// the backend accepts for YES/no-reasons regeneration.
export const regenerateDraft = (leadId: string, reasons?: string[]) =>
  client
    .post<Assessment>(`/assessments/${leadId}/regenerate-draft`, reasons?.length ? { reasons } : undefined)
    .then((r) => r.data);

// Zero-LLM prebuilt rejection template (issue #232) -- sits beside
// regenerateDraft above rather than replacing it. `language` ("en"/"ar")
// overrides the backend's applicant-language auto-detection; omit to use it.
export const renderRejectionTemplate = (
  leadId: string,
  params?: { reasons?: string[]; language?: "en" | "ar" },
) =>
  client
    .post<Assessment>(`/assessments/${leadId}/rejection-template`, {
      ...(params?.reasons?.length ? { reasons: params.reasons } : {}),
      ...(params?.language ? { language: params.language } : {}),
    })
    .then((r) => r.data);
