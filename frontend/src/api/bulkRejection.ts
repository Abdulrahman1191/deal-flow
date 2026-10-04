import client from "./client";

export interface BulkSendRejectionPreviewItem {
  lead_id: string;
  company_name: string | null;
  recipient_email: string | null;
  draft_subject: string | null;
  draft_excerpt: string | null;
  eligible: boolean;
  reason: string | null;
}

export interface BulkSendRejectionPreviewResult {
  eligible_count: number;
  items: BulkSendRejectionPreviewItem[];
}

export interface BulkSendRejectionSkipped {
  lead_id: string;
  reason: string;
}

export interface BulkSendRejectionResult {
  batch_id: string;
  queued: number;
  skipped: BulkSendRejectionSkipped[];
}

export interface BulkSendRejectionBatchItem {
  lead_id: string;
  company_name: string | null;
  status: "queued" | "sent" | "failed" | "skipped";
  reason: string | null;
}

export interface BulkSendRejectionBatchStatus {
  batch_id: string;
  sent: number;
  failed: number;
  skipped: number;
  queued: number;
  items: BulkSendRejectionBatchItem[];
}

/** Human labels for the backend's machine-readable eligibility reasons
 * (app/services/bulk_rejection.py) — kept in sync by hand since the backend
 * intentionally returns short codes, not display text. */
export const REJECTION_INELIGIBLE_REASONS: Record<string, string> = {
  invalid_lead_id: "Invalid lead",
  not_found: "Lead not found",
  no_assessment: "No draft",
  not_reject_bucket: "Not in Reject",
  already_sent: "Already sent",
  no_recipient_email: "No recipient email on file",
  stale_or_missing_draft: "Draft doesn't match bucket",
  lead_archived_or_converted: "Already archived or converted",
};

export const describeIneligibleReason = (reason: string | null) =>
  (reason && REJECTION_INELIGIBLE_REASONS[reason]) || reason || "Not eligible";

export const previewBulkSendRejection = (leadIds: string[]) =>
  client
    .post<BulkSendRejectionPreviewResult>("/leads/bulk-send-rejection/preview", { lead_ids: leadIds })
    .then((r) => r.data);

export const executeBulkSendRejection = (leadIds: string[], confirmCount: number) =>
  client
    .post<BulkSendRejectionResult>("/leads/bulk-send-rejection", {
      lead_ids: leadIds,
      confirm_count: confirmCount,
    })
    .then((r) => r.data);

export const fetchBulkSendRejectionBatch = (batchId: string) =>
  client.get<BulkSendRejectionBatchStatus>(`/leads/bulk-send-rejection/${batchId}`).then((r) => r.data);
