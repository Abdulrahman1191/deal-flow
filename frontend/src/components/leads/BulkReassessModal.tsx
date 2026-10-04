import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../shared/Modal";
import {
  executeBulkReassess,
  fetchBulkReassessProgress,
  previewBulkReassess,
  type BulkReassessProgress,
  type BulkReassessResult,
} from "../../api/bulkReassess";

interface Props {
  leadIds: string[];
  onClose: () => void;
  /** Called once the batch is queued — lets the board clear its selection
   * immediately, same as bulk archive does on success. */
  onQueued?: () => void;
}

/** Render both 409 refusals (issue #204) as plain language, and fall back to
 * the backend's own (already human-written) detail for anything else —
 * e.g. the batch-cap refusal — rather than a raw axios error. */
function describeError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: string } | undefined)?.detail;
    if (err.response?.status === 409 && detail?.includes("DeepSeek is unavailable")) {
      return "Assessments are paused — DeepSeek is unavailable.";
    }
    if (err.response?.status === 409 && detail?.toLowerCase().includes("no longer matches")) {
      return "The board changed since your preview — please re-run the preview.";
    }
    if (detail) return detail;
  }
  return "Couldn't run the reassessment — please try again.";
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-background border border-border rounded-xl p-3">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className="text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

export default function BulkReassessModal({ leadIds, onClose, onQueued }: Props) {
  const qc = useQueryClient();
  const leadIdsKey = [...leadIds].sort().join(",");
  const [force, setForce] = useState(false);
  const [execResult, setExecResult] = useState<BulkReassessResult | null>(null);
  const invalidatedRef = useRef(false);

  const previewMutation = useMutation({
    mutationFn: () => previewBulkReassess({ lead_ids: leadIds, force }),
  });

  const preview = previewMutation.data;

  // A confirm must never run against a stale preview — re-fetch whenever the
  // selection or the "re-run unchanged" checkbox changes.
  const executeMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("No preview");
      return executeBulkReassess({ lead_ids: leadIds, force, confirm_count: preview.matched });
    },
    onSuccess: (data) => {
      setExecResult(data);
      onQueued?.();
    },
    onError: (err) => {
      // The board moved since preview — don't let a stale preview back a
      // second confirm attempt; the partner has to re-run it first.
      if (describeError(err).includes("re-run the preview")) {
        previewMutation.reset();
      }
    },
  });

  useEffect(() => {
    setExecResult(null);
    executeMutation.reset();
    previewMutation.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadIdsKey, force]);

  const batchId = execResult?.batch_id ?? null;
  const polling = !!batchId && execResult!.queued > 0;

  const progressQuery = useQuery<BulkReassessProgress>({
    queryKey: ["bulk-reassess-progress", batchId],
    queryFn: () => fetchBulkReassessProgress(batchId!),
    enabled: polling,
    refetchInterval: (query) => (query.state.data && query.state.data.pending === 0 ? false : 1500),
  });

  const progress = progressQuery.data;
  const finished = !!progress && progress.pending === 0;

  useEffect(() => {
    if (finished && !invalidatedRef.current) {
      invalidatedRef.current = true;
      qc.invalidateQueries({ queryKey: ["leads"] });
    }
  }, [finished, qc]);

  const executeError = executeMutation.isError ? describeError(executeMutation.error) : null;
  const executeErrorIsStaleCount = executeError?.includes("re-run the preview") ?? false;

  const previewUnavailable = previewMutation.isError;
  const canConfirm = !!preview && !preview.breaker_open && !executeMutation.isPending && !execResult;

  return (
    <Modal title="Reassess selected" onClose={onClose}>
      <div className="space-y-4">
        {!execResult && (
          <>
            <p className="text-sm text-foreground">
              Re-run the assessor on {leadIds.length} selected lead{leadIds.length === 1 ? "" : "s"}.
            </p>

            {previewMutation.isPending && (
              <p className="text-sm text-muted-foreground">Loading preview…</p>
            )}

            {previewUnavailable && (
              <div className="text-sm text-error bg-error/10 rounded-lg px-3 py-2">
                Couldn't load the preview — please try again.
              </div>
            )}

            {preview && (
              <>
                {preview.breaker_open && (
                  <div className="text-sm text-error bg-error/10 rounded-lg px-3 py-2">
                    Assessments are paused — DeepSeek is unavailable.
                    {preview.breaker_reason ? ` (${preview.breaker_reason})` : ""}
                  </div>
                )}
                <div className="grid grid-cols-3 gap-3">
                  <StatTile label="Matched" value={preview.matched} />
                  <StatTile label="Have new info" value={preview.changed} />
                  <StatTile label="Would skip (unchanged)" value={preview.would_skip} />
                </div>
                {preview.in_flight > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {preview.in_flight} already running and will be skipped either way.
                  </p>
                )}
                <p className="text-sm text-foreground">
                  Would queue <span className="font-semibold">{preview.would_queue}</span> for
                  reassessment.
                </p>

                <label className="flex items-start gap-2 text-sm cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={force}
                    onChange={(e) => setForce(e.target.checked)}
                    className="h-3.5 w-3.5 mt-0.5 rounded border-border accent-primary"
                  />
                  <span className="text-foreground">
                    Re-run even where nothing has changed
                    <span className="block text-xs text-muted-foreground mt-0.5">
                      Off by default — the assessor is deterministic, so unchanged leads normally
                      return the same verdict.
                    </span>
                  </span>
                </label>
              </>
            )}

            {executeError && (
              <div className="text-sm text-error bg-error/10 rounded-lg px-3 py-2">
                {executeError}
                {executeErrorIsStaleCount && (
                  <button
                    onClick={() => {
                      executeMutation.reset();
                      previewMutation.mutate();
                    }}
                    className="ml-2 underline hover:no-underline"
                  >
                    Re-run preview
                  </button>
                )}
              </div>
            )}

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium rounded-lg text-muted-foreground hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => executeMutation.mutate()}
                disabled={!canConfirm}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {executeMutation.isPending
                  ? "Queueing…"
                  : preview
                    ? `Reassess ${preview.would_queue} leads`
                    : "Reassess selected"}
              </button>
            </div>
          </>
        )}

        {execResult && (
          <>
            <div className="grid grid-cols-3 gap-3">
              <StatTile label="Queued" value={execResult.queued} />
              <StatTile label="Skipped (unchanged)" value={execResult.skipped_unchanged} />
              <StatTile label="Skipped (in progress)" value={execResult.skipped_in_flight} />
            </div>

            {execResult.failed.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-error uppercase tracking-wider mb-2">
                  Failed to queue ({execResult.failed.length})
                </h3>
                <ul className="space-y-1.5">
                  {execResult.failed.map((f) => (
                    <li key={f.lead_id} className="text-sm bg-error/10 text-error rounded-lg px-3 py-2">
                      <span className="font-mono text-xs">{f.lead_id}</span> — {f.error}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {execResult.queued === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing was queued.</p>
            ) : !progress || progress.pending > 0 ? (
              <div className="space-y-2">
                <p className="text-sm text-foreground">Running…</p>
                <div className="grid grid-cols-3 gap-3">
                  <StatTile label="Done" value={progress?.assessed ?? 0} />
                  <StatTile label="Pending" value={progress?.pending ?? execResult.queued} />
                  <StatTile label="Failed" value={progress?.failed ?? 0} />
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-sm text-foreground">
                  <span className="font-semibold">{progress.bucket_changed}</span> lead
                  {progress.bucket_changed === 1 ? "" : "s"} changed bucket
                  {progress.bucket_unchanged > 0 ? ` · ${progress.bucket_unchanged} unchanged` : ""}
                  {progress.failed > 0 ? ` · ${progress.failed} failed` : ""}.
                </p>
                <p className="text-xs text-muted-foreground">
                  The board has been refreshed with the new buckets.
                </p>
              </div>
            )}

            <div className="flex items-center justify-end pt-2">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors"
              >
                Done
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
