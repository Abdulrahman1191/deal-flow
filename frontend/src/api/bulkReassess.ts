import client from "./client";

export type ReassessBucket = "YES" | "MAYBE" | "REJECT";

export interface BulkReassessPreviewRequest {
  lead_ids?: string[];
  bucket?: ReassessBucket;
  assessed_before?: string;
  force?: boolean;
}

export interface BulkReassessPreviewResult {
  matched: number;
  changed: number;
  would_skip: number;
  in_flight: number;
  would_queue: number;
  breaker_open: boolean;
  breaker_reason: string | null;
}

export interface BulkReassessFailure {
  lead_id: string;
  error: string;
}

export interface BulkReassessResult {
  batch_id: string;
  matched: number;
  queued: number;
  skipped_unchanged: number;
  skipped_in_flight: number;
  failed: BulkReassessFailure[];
}

export interface BulkReassessProgress {
  batch_id: string;
  queued: number;
  assessed: number;
  pending: number;
  failed: number;
  bucket_changed: number;
  bucket_unchanged: number;
}

export const previewBulkReassess = (body: BulkReassessPreviewRequest) =>
  client.post<BulkReassessPreviewResult>("/leads/bulk-reassess/preview", body).then((r) => r.data);

export const executeBulkReassess = (body: BulkReassessPreviewRequest & { confirm_count: number }) =>
  client.post<BulkReassessResult>("/leads/bulk-reassess", body).then((r) => r.data);

export const fetchBulkReassessProgress = (batchId: string) =>
  client.get<BulkReassessProgress>(`/leads/bulk-reassess/${batchId}`).then((r) => r.data);
