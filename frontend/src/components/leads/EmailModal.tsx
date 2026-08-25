import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { regenerateDraft, sendEmail, updateDraft } from "../../api/assessments";
import { useToast } from "../shared/Toast";
import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onClose: () => void;
}

// The draft_type a draft must have to be sendable for a given effective
// bucket — mirrors the backend's _EXPECTED_DRAFT_TYPE (issue #150).
const EXPECTED_DRAFT_TYPE: Record<string, "meeting_request" | "rejection" | null> = {
  YES: "meeting_request",
  REJECT: "rejection",
  MAYBE: null,
};

function extractDetail(err: unknown): string | undefined {
  return axios.isAxiosError(err) ? (err.response?.data as { detail?: string } | undefined)?.detail : undefined;
}

export default function EmailModal({ lead, onClose }: Props) {
  const { assessment } = lead;
  const qc = useQueryClient();
  const toast = useToast();

  const effectiveBucket = assessment?.user_override ?? assessment?.bucket;
  const expectedDraftType = effectiveBucket ? EXPECTED_DRAFT_TYPE[effectiveBucket] : null;
  // Stale = this card's draft was never written for the bucket the lead is at
  // right now — missing, wrong draft_type, or (per draft_bucket) explicitly
  // recorded against a different bucket. Mirrors the backend's send-time
  // guard so the UI never shows a contradictory draft as if it were valid.
  const isStale =
    !!effectiveBucket &&
    (!assessment?.draft_body ||
      assessment?.draft_type !== expectedDraftType ||
      (!!assessment?.draft_bucket && assessment.draft_bucket !== effectiveBucket));

  const [subject, setSubject] = useState(isStale ? "" : assessment?.draft_subject ?? "");
  const [body, setBody] = useState(isStale ? "" : assessment?.draft_body ?? "");
  const [error, setError] = useState<string | null>(null);

  // Signature of the draft currently reflected in the fields above, so we can
  // tell "the draft changed under us" (a bucket override or background
  // reassessment landing while this modal is open) apart from the user's own
  // edits, and re-sync instead of silently leaving stale text on screen.
  const loadedSignature = useRef(`${assessment?.draft_type}:${assessment?.draft_body}`);

  const regenMutation = useMutation({
    mutationFn: () => regenerateDraft(lead.id),
    onSuccess: (data) => {
      loadedSignature.current = `${data.draft_type}:${data.draft_body}`;
      setSubject(data.draft_subject ?? "");
      setBody(data.draft_body ?? "");
      setError(null);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (err: unknown) => {
      const msg = extractDetail(err);
      setError(
        msg ?? (isStale ? "Couldn't regenerate the draft — try again." : "Couldn't regenerate draft — write it manually below."),
      );
    },
  });

  // Auto-regenerate whenever the modal is showing a stale draft: on open with
  // a missing/mismatched draft (e.g. a silent regen failure), and again if
  // the effective bucket changes out from under an already-open modal. Never
  // fires for MAYBE — there's no email to write for it.
  useEffect(() => {
    const signature = `${assessment?.draft_type}:${assessment?.draft_body}`;
    if (signature !== loadedSignature.current) {
      loadedSignature.current = signature;
      if (!isStale) {
        setSubject(assessment?.draft_subject ?? "");
        setBody(assessment?.draft_body ?? "");
      } else {
        setSubject("");
        setBody("");
      }
      setError(null);
    }
    if (isStale && effectiveBucket !== "MAYBE" && !regenMutation.isPending) {
      regenMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessment?.draft_type, assessment?.draft_body, effectiveBucket]);

  const sendMutation = useMutation({
    mutationFn: async () => {
      setError(null);
      const subjectChanged = subject !== (assessment?.draft_subject ?? "");
      const bodyChanged = body !== (assessment?.draft_body ?? "");
      if (subjectChanged || bodyChanged) {
        await updateDraft(lead.id, {
          ...(subjectChanged ? { draft_subject: subject } : {}),
          ...(bodyChanged ? { draft_body: body } : {}),
        });
      }
      await sendEmail(lead.id);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["send-queue"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
      onClose();
    },
    onError: (err: unknown) => {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      const msg = extractDetail(err);
      if (status === 409) {
        // The backend's stale-draft guard (issue #150) — the bucket moved
        // again between opening this modal and clicking Send. Refresh so the
        // modal picks up the new effective bucket and re-triggers regen.
        toast(msg ?? "This draft is stale for the lead's current decision — regenerate it before sending.");
        qc.invalidateQueries({ queryKey: ["leads"] });
        return;
      }
      setError(msg ?? "Failed to send — try again.");
    },
  });

  const bucketColor = effectiveBucket === "YES" ? "text-success" : "text-error";
  const headerLabel = (() => {
    const dt = assessment?.draft_type;
    if (dt === "meeting_request") return "Meeting Request";
    if (dt === "rejection") return "Rejection";
    return effectiveBucket === "YES" ? "Meeting Request" : "Rejection";
  })();

  const generating = regenMutation.isPending;
  const fieldsDisabled = generating || isStale;
  const canSend = !isStale && !generating && !sendMutation.isPending && !!body.trim() && !!subject.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-card border border-border rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh] animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div>
            <p className="text-foreground font-semibold">{lead.company_name}</p>
            <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${bucketColor}`}>
              {headerLabel}
            </p>
            {generating && (
              <p className="text-[10px] text-info mt-1 animate-pulse">
                AI is writing the draft…
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => regenMutation.mutate()}
              disabled={generating || effectiveBucket === "MAYBE"}
              className={`text-xs transition-colors disabled:opacity-50 ${
                isStale ? "font-semibold text-warning hover:text-warning" : "text-info hover:text-info"
              }`}
              title="Ask the AI to rewrite this draft"
              data-testid="regenerate-draft-btn"
            >
              {generating ? "…" : "Regenerate ↻"}
            </button>
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground text-lg leading-none"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Editable email */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {isStale && !generating && (
            <div
              className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-warning space-y-1"
              data-testid="stale-draft-notice"
            >
              <p className="font-medium">
                This draft was written for a different decision — regenerate it.
              </p>
              <p className="text-warning/80">
                The lead's current call is <strong>{effectiveBucket}</strong>, but the saved draft
                {assessment?.draft_type ? ` was written as "${assessment.draft_type}"` : " is missing"}.
                Click Regenerate above before sending.
              </p>
            </div>
          )}
          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1">
              Subject
            </label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={fieldsDisabled}
              className={`w-full bg-background border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-border disabled:opacity-50 ${!subject.trim() && !fieldsDisabled ? "border-error" : "border-border"}`}
            />
            {!subject.trim() && !fieldsDisabled && (
              <p className="text-[10px] text-error mt-1">Subject is required</p>
            )}
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1">
              Body
            </label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={fieldsDisabled}
              rows={12}
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-border resize-none font-mono leading-relaxed disabled:opacity-50"
            />
          </div>
          {error && <p className="text-xs text-error">{error}</p>}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border">
          <button
            onClick={onClose}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => sendMutation.mutate()}
            disabled={!canSend}
            title={isStale ? "Regenerate the draft first — it doesn't match the lead's current decision" : undefined}
            className="px-5 py-2 text-sm font-medium rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {sendMutation.isPending ? "Sending…" : "Send Email"}
          </button>
        </div>
      </div>
    </div>
  );
}
