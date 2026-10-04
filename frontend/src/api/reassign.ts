import client from "./client";

export type ReassignBucket = "YES" | "MAYBE" | "REJECT";

export interface BulkReassignPreviewRequest {
  from_owner: string;
  to_owners: string[];
  bucket?: ReassignBucket;
  include_converted: boolean;
}

export interface BulkReassignPreviewResult {
  count: number;
  by_status: Record<string, number>;
  by_bucket: Record<string, number>;
  by_target: Record<string, number>;
}

export interface BulkReassignFailure {
  lead_id: string;
  error: string;
}

export interface BulkReassignResult {
  batch_id: string;
  moved: number;
  by_target: Record<string, number>;
  failed: BulkReassignFailure[];
}

export const previewBulkReassign = (body: BulkReassignPreviewRequest) =>
  client
    .post<BulkReassignPreviewResult>("/leads/reassign/preview", body)
    .then((r) => r.data);

export const executeBulkReassign = (body: BulkReassignPreviewRequest & { confirm_count: number }) =>
  client.post<BulkReassignResult>("/leads/reassign", body).then((r) => r.data);
