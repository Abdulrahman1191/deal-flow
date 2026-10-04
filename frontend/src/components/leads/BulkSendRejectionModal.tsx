import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../shared/Modal";
import {
  describeIneligibleReason,
  executeBulkSendRejection,
  fetchBulkSendRejectionBatch,
  previewBulkSendRejection,
  type BulkSendRejectionBatchStatus,
  type BulkSendRejectionPreviewItem,
  type BulkSendRejectionResult,
} from "../../api/bulkRejection";

interface Props {
  leadIds: string[];
  onClose: () => void;
  /** Called once the batch is queued — lets the board clear its selection
   * immediately, same as bulk archive / bulk reassess do on success. */
  onQueued?: () => void;
}

type Step = "review" | "confirm" | "result";

/** Render the 409 refusal (confirm_count no longer matches — the board moved
 * since the review was opened) as plain language, and fall back to the
 * backend's own detail for anything else. */
function describeError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: string } | undefined)?.detail;
    if (err.response?.status === 409 && detail?.toLowerCase().includes("no longer matches")) {
      return "The board changed since you opened this review — please re-run the review.";
    }
    if (detail) return detail;
  }
  return "Couldn't send the rejection emails — please try again.";
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-background border border-border rounded-xl p-3">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className="text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function DraftExcerpt({ item }: { item: BulkSendRejectionPreviewItem }) {
  const [open, setOpen] = useState(false);
  if (!item.draft_excerpt) return <span className="text-muted-foreground">—</span>;
  return (
    <button
      type="button"
      onClick={() => setOpen((o) => !o)}
      className="text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
      {open ? item.draft_excerpt : `${item.draft_excerpt.slice(0, 60)}${item.draft_excerpt.length > 60 ? "…" : ""}`}
      <span className="ml-1 text-info">{open ? " (collapse)" : " (expand)"}</span>
    </button>
  );
}

export default function BulkSendRejectionModal({ leadIds, onClose, onQueued }: Props) {
  const qc = useQueryClient();
  const leadIdsKey = [...leadIds].sort().join(",");
  const [step, setStep] = useState<Step>("review");
  // Eligible lead_ids the partner has unchecked from the default "everyone
  // eligible is included" selection — deselecting a row must change the
  // count (and the lead_ids) passed to the API.
  const [excludedIds, setExcludedIds] = useState<Set<string>>(new Set());
  const [execResult, setExecResult] = useState<BulkSendRejectionResult | null>(null);
  const invalidatedRef = useRef(false);

  const previewMutation = useMutation({
    mutationFn: () => previewBulkSendRejection(leadIds),
  });

  const preview = previewMutation.data;

  useEffect(() => {
    setStep("review");
    setExcludedIds(new Set());
    setExecResult(null);
    executeMutation.reset();
    previewMutation.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadIdsKey]);

  const eligibleItems = preview?.items.filter((i) => i.eligible) ?? [];
  const ineligibleItems = preview?.items.filter((i) => !i.eligible) ?? [];
  const selectedIds = eligibleItems.filter((i) => !excludedIds.has(i.lead_id)).map((i) => i.lead_id);
  const count = selectedIds.length;

  const toggleRow = (leadId: string) => {
    setExcludedIds((prev) => {
      const next = new Set(prev);
      if (next.has(leadId)) next.delete(leadId);
      else next.add(leadId);
      return next;
    });
  };

  const executeMutation = useMutation({
    mutationFn: () => executeBulkSendRejection(selectedIds, count),
    onSuccess: (data) => {
      setExecResult(data);
      setStep("result");
      onQueued?.();
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
      qc.invalidateQueries({ queryKey: ["send-queue"] });
    },
    onError: (err) => {
      // The board moved since the review was opened — don't let a stale
      // review back a second send attempt; the partner has to re-run it.
      if (describeError(err).includes("re-run the review")) {
        setStep("review");
        previewMutation.mutate();
      }
    },
  });

  const batchId = execResult?.batch_id ?? null;
  const polling = !!batchId && execResult!.queued > 0;

  const progressQuery = useQuery<BulkSendRejectionBatchStatus>({
    queryKey: ["bulk-send-rejection-progress", batchId],
    queryFn: () => fetchBulkSendRejectionBatch(batchId!),
    enabled: polling,
    refetchInterval: (query) => (query.state.data && query.state.data.queued === 0 ? false : 1500),
  });

  const progress = progressQuery.data;
  const finished = !!progress && progress.queued === 0;

  useEffect(() => {
    if (finished && !invalidatedRef.current) {
      invalidatedRef.current = true;
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
      qc.invalidateQueries({ queryKey: ["send-queue"] });
    }
  }, [finished, qc]);

  const executeError = executeMutation.isError ? describeError(executeMutation.error) : null;
  const previewUnavailable = previewMutation.isError;
  const failedItems = progress?.items.filter((i) => i.status === "failed") ?? [];

  return (
    <Modal title="Send rejection emails" onClose={onClose}>
      <div className="space-y-4">
        {step === "review" && (
          <>
            <p className="text-sm text-foreground">
              Review {leadIds.length} selected lead{leadIds.length === 1 ? "" : "s"} before sending.
            </p>

            {previewMutation.isPending && (
              <p className="text-sm text-muted-foreground">Loading preview…</p>
            )}

            {previewUnavailable && (
              <div className="text-sm text-error bg-error/10 rounded-lg px-3 py-2">
                Couldn't load the review — please try again.
              </div>
            )}

            {preview && (
              <>
                <p className="text-xs text-muted-foreground bg-info/10 text-info rounded-lg px-3 py-2">
                  Each founder receives their own individual email — applicants never see each
                  other's addresses.
                </p>

                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-foreground uppercase tracking-wider">
                    Will send ({count})
                  </h3>
                </div>

                <div className="border border-border rounded-xl max-h-64 overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-card border-b border-border text-xs text-muted-foreground uppercase tracking-wider">
                      <tr>
                        <th className="w-8 px-3 py-2" />
                        <th className="text-left px-3 py-2">Company</th>
                        <th className="text-left px-3 py-2">Recipient</th>
                        <th className="text-left px-3 py-2">Subject</th>
                        <th className="text-left px-3 py-2">Draft</th>
                      </tr>
                    </thead>
                    <tbody>
                      {eligibleItems.map((item) => (
                        <tr key={item.lead_id} className="border-b border-border last:border-0">
                          <td className="px-3 py-2">
                            <input
                              type="checkbox"
                              checked={!excludedIds.has(item.lead_id)}
                              onChange={() => toggleRow(item.lead_id)}
                              className="h-3.5 w-3.5 rounded border-border accent-primary"
                              data-testid="rejection-row-checkbox"
                            />
                          </td>
                          <td className="px-3 py-2 text-foreground">{item.company_name ?? "—"}</td>
                          <td className="px-3 py-2 text-muted-foreground">{item.recipient_email ?? "—"}</td>
                          <td className="px-3 py-2 text-muted-foreground">{item.draft_subject ?? "—"}</td>
                          <td className="px-3 py-2"><DraftExcerpt item={item} /></td>
                        </tr>
                      ))}
                      {eligibleItems.length === 0 && (
                        <tr>
                          <td colSpan={5} className="px-3 py-6 text-center text-xs text-muted-foreground">
                            No eligible leads in this selection.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {ineligibleItems.length > 0 && (
                  <div className="border border-dashed border-warning/40 bg-warning/5 rounded-xl p-3 space-y-1.5">
                    <h3 className="text-xs font-semibold text-warning uppercase tracking-wider">
                      Ineligible ({ineligibleItems.length}) — can't be included
                    </h3>
                    <ul className="space-y-1">
                      {ineligibleItems.map((item) => (
                        <li key={item.lead_id} className="text-xs text-warning flex items-center justify-between gap-3">
                          <span className="text-foreground">{item.company_name ?? item.lead_id}</span>
                          <span>{describeIneligibleReason(item.reason)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium rounded-lg text-muted-foreground hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => setStep("confirm")}
                disabled={!preview || count === 0}
                data-testid="rejection-review-continue-btn"
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Continue
              </button>
            </div>
          </>
        )}

        {step === "confirm" && (
          <>
            <p className="text-sm text-muted-foreground">
              {count} founder{count === 1 ? "" : "s"} will each receive one individually addressed
              rejection email.
            </p>

            {executeError && (
              <div className="text-sm text-error bg-error/10 rounded-lg px-3 py-2">{executeError}</div>
            )}

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setStep("review")}
                disabled={executeMutation.isPending}
                className="px-4 py-2 text-sm font-medium rounded-lg text-muted-foreground hover:text-foreground transition-colors"
              >
                Back
              </button>
              <button
                onClick={() => executeMutation.mutate()}
                disabled={executeMutation.isPending}
                data-testid="rejection-confirm-send-btn"
                className="px-4 py-2 text-sm font-medium rounded-lg bg-error text-white hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {executeMutation.isPending
                  ? "Sending…"
                  : `Send ${count} separate rejection email${count === 1 ? "" : "s"}? Each founder receives their own email. This cannot be undone.`}
              </button>
            </div>
          </>
        )}

        {step === "result" && execResult && (
          <>
            <div className="grid grid-cols-3 gap-3">
              <StatTile label="Queued" value={execResult.queued} />
              <StatTile label="Sent" value={progress?.sent ?? 0} />
              <StatTile label="Failed" value={progress?.failed ?? 0} />
            </div>

            {execResult.skipped.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                  Skipped at send time ({execResult.skipped.length})
                </h3>
                <ul className="space-y-1.5">
                  {execResult.skipped.map((s) => (
                    <li key={s.lead_id} className="text-sm bg-muted/50 text-muted-foreground rounded-lg px-3 py-2">
                      {describeIneligibleReason(s.reason)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {execResult.queued === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing was queued.</p>
            ) : !progress || progress.queued > 0 ? (
              <p className="text-sm text-foreground">Sending…</p>
            ) : (
              <p className="text-sm text-foreground">
                <span className="font-semibold">{progress.sent}</span> sent
                {progress.failed > 0 ? ` · ${progress.failed} failed` : ""}
                {progress.skipped > 0 ? ` · ${progress.skipped} skipped` : ""}.
              </p>
            )}

            {failedItems.length > 0 && (
              <div data-testid="rejection-failed-list">
                <h3 className="text-xs font-semibold text-error uppercase tracking-wider mb-2">
                  Failed ({failedItems.length})
                </h3>
                <ul className="space-y-1.5">
                  {failedItems.map((item) => (
                    <li key={item.lead_id} className="text-sm bg-error/10 text-error rounded-lg px-3 py-2">
                      <span className="font-medium">{item.company_name ?? item.lead_id}</span> —{" "}
                      {describeIneligibleReason(item.reason)}
                    </li>
                  ))}
                </ul>
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
