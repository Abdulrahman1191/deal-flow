import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { regenerateDraft, renderRejectionTemplate, sendEmail, updateDraft } from "../../api/assessments";
import { useToast } from "../shared/Toast";
import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onClose: () => void;
}

// Four plain-language, single-select reasons (issue #247) replacing the 13
// UNQUAL_REASON_LABELS chips -- each maps to exactly one of the backend's
// four prebuilt templates (app/services/rejection_templates.py). The
// `reason` sent here is one canonical UNQUAL_REASON_OPTIONS label so
// select_template() still resolves correctly; the rating/override step
// (AssessmentRating.reason_tags / BucketOverride.reason_tags) remains the
// path that drives Copper's Unqualification Reasons field.
const REASON_BUTTONS: { reason: string; label: string; hint: string }[] = [
  { reason: "Market size", label: "Market size", hint: "we don't believe the market is large enough" },
  {
    reason: "Conflict of interest",
    label: "Conflict of interest",
    hint: "we hold a conflicting position, never named",
  },
  { reason: "Lack of traction", label: "No clear traction", hint: "not enough evidence yet" },
  { reason: "Other", label: "Not a fit right now", hint: "outside our current investment strategy" },
];

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

  // Single-select reason (issue #247) -- REJECT only. Preselected only on an
  // exact match against one of the four REASON_BUTTONS values, so reopening
  // the modal restores a prior pick made through this same picker without
  // reviving any of the retired 13-chip labels.
  const [selectedReason, setSelectedReason] = useState<string | null>(
    () => (assessment?.rejection_reasons ?? []).find((r) => REASON_BUTTONS.some((b) => b.reason === r)) ?? null,
  );
  const isReject = effectiveBucket === "REJECT";
  // Kept as its own array for the /regenerate-draft and template calls below.
  const canonicalSelectedReasons = selectedReason ? [selectedReason] : [];

  // EN/AR toggle (issue #232) -- defaults to whatever the backend's
  // zero-LLM template endpoint auto-detected from the applicant's own text
  // (claude_agent.detect_applicant_language) the first time it renders;
  // once the partner picks a value it stays pinned to that override.
  const [language, setLanguage] = useState<"en" | "ar" | null>(assessment?.draft_language ?? null);

  // Clicking the active button clears it; clicking another switches to it.
  const toggleReason = (reason: string) =>
    setSelectedReason((prev) => (prev === reason ? null : reason));

  // Signature of the draft currently reflected in the fields above, so we can
  // tell "the draft changed under us" (a bucket override or background
  // reassessment landing while this modal is open) apart from the user's own
  // edits, and re-sync instead of silently leaving stale text on screen.
  const loadedSignature = useRef(`${assessment?.draft_type}:${assessment?.draft_body}`);

  const regenMutation = useMutation({
    mutationFn: (reasons?: string[]) => regenerateDraft(lead.id, reasons),
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

  // Zero-LLM prebuilt rejection template (issue #232) -- the default path for
  // a REJECT draft. "Regenerate with AI" (regenMutation above) stays as an
  // explicit, demoted opt-in for bespoke wording.
  const templateMutation = useMutation({
    mutationFn: (params: { reasons?: string[]; language?: "en" | "ar" }) =>
      renderRejectionTemplate(lead.id, params),
    onSuccess: (data) => {
      loadedSignature.current = `${data.draft_type}:${data.draft_body}`;
      setSubject(data.draft_subject ?? "");
      setBody(data.draft_body ?? "");
      setError(null);
      if (data.draft_language) setLanguage(data.draft_language);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (err: unknown) => {
      setError(extractDetail(err) ?? "Couldn't render the template — try again.");
    },
  });

  // Auto-render whenever the modal is showing a stale draft: on open with a
  // missing/mismatched draft (e.g. a silent regen failure), and again if the
  // effective bucket changes out from under an already-open modal. A REJECT
  // lead renders from the zero-LLM template by default; YES still uses the
  // AI path (unchanged, out of scope for issue #232). Never fires for
  // MAYBE — there's no email to write for it.
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
    if (isStale && effectiveBucket === "REJECT" && !templateMutation.isPending) {
      templateMutation.mutate({ reasons: canonicalSelectedReasons, language: language ?? undefined });
    } else if (isStale && effectiveBucket === "YES" && !regenMutation.isPending) {
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

  const rendering = templateMutation.isPending;
  const generating = regenMutation.isPending || rendering;
  const fieldsDisabled = generating || isStale;
  const canSend = !isStale && !generating && !sendMutation.isPending && !!body.trim() && !!subject.trim();

  // Portaled to document.body (issue #247) -- rendered inline this div's
  // `fixed inset-0` resolved against the lead card's box instead of the
  // viewport, because the card's `hover:-translate-y-*` makes it a
  // transformed ancestor, and any transformed ancestor becomes the
  // containing block for a `position: fixed` descendant. A portal has no
  // card ancestor at all, so no hover state can ever reintroduce the bug.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in"
      data-testid="email-modal-overlay"
    >
      <div className="bg-card border border-border rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col h-[90vh] max-h-[760px] animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div>
            <p className="text-foreground font-semibold">{lead.company_name}</p>
            <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${bucketColor}`}>
              {headerLabel}
            </p>
            {regenMutation.isPending && (
              <p className="text-[10px] text-info mt-1 animate-pulse">
                AI is writing the draft…
              </p>
            )}
            {rendering && (
              <p className="text-[10px] text-info mt-1 animate-pulse">
                Rendering template…
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {!isReject && (
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
            )}
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground text-lg leading-none"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Reason picker (issue #247) — REJECT only; a YES lead's modal has
            no reasons section at all. Four single-select, plain-language
            buttons replacing the 13-chip UNQUAL_REASON_LABELS picker, which
            duplicated the rating/override step and left most chips disabled.
            That step (AssessmentRating.reason_tags / BucketOverride.reason_tags)
            is still what drives Copper's Unqualification Reasons field. */}
        {isReject && (
          <div className="px-5 py-4 border-b border-border space-y-3 bg-muted/20 shrink-0">
            <div className="flex items-center justify-between">
              <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Reason
              </label>
              {/* EN/AR toggle (issue #232) -- defaults to the backend's
                  detected applicant language; re-renders the template
                  immediately on change. */}
              <div className="flex rounded-md border border-border overflow-hidden" data-testid="language-toggle">
                {(["en", "ar"] as const).map((lang) => (
                  <button
                    key={lang}
                    type="button"
                    disabled={rendering}
                    onClick={() => {
                      setLanguage(lang);
                      templateMutation.mutate({ reasons: canonicalSelectedReasons, language: lang });
                    }}
                    data-testid={`language-toggle-${lang}`}
                    className={`text-[10px] uppercase px-2 py-1 transition-colors disabled:opacity-50 ${
                      (language ?? "en") === lang
                        ? "bg-primary text-white"
                        : "bg-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {lang}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {REASON_BUTTONS.map(({ reason, label, hint }) => {
                const isOn = selectedReason === reason;
                return (
                  <button
                    key={reason}
                    type="button"
                    aria-pressed={isOn}
                    onClick={() => toggleReason(reason)}
                    className={`text-left text-xs px-3 py-2 rounded-lg transition-colors border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 ${
                      isOn
                        ? "bg-error/20 text-error border-error"
                        : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
                    }`}
                  >
                    <span className="block font-medium">{label}</span>
                    <span className="block opacity-70">{hint}</span>
                  </button>
                );
              })}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  templateMutation.mutate({ reasons: canonicalSelectedReasons, language: language ?? undefined })
                }
                disabled={generating}
                className="text-xs font-medium px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
                data-testid="render-template-btn"
              >
                {rendering ? "Rendering…" : "Use template with these reasons"}
              </button>
              {/* "Regenerate with AI" (issue #232) -- demoted to an explicit
                  secondary action for the rare lead that needs bespoke
                  wording (issue #223's reason-aware regeneration, kept but
                  no longer the default). */}
              <button
                type="button"
                onClick={() => regenMutation.mutate(canonicalSelectedReasons)}
                disabled={generating}
                title="Ask the AI to write a bespoke draft instead of the template"
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
                data-testid="regenerate-with-reasons-btn"
              >
                {regenMutation.isPending ? "Regenerating…" : "Regenerate with AI ↻"}
              </button>
            </div>
          </div>
        )}

        {/* Editable email -- the only scrollable region, so the body (the
            whole point of this modal) always gets whatever height is left
            after the fixed header/reasons/footer rather than a fixed row
            count (issue #243). */}
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-3 flex flex-col">
          {isStale && !generating && (
            <div
              className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-warning space-y-1 shrink-0"
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
          <div className="shrink-0">
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
          <div className="flex-1 flex flex-col min-h-0">
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1 shrink-0">
              Body
            </label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={fieldsDisabled}
              className="w-full flex-1 min-h-[160px] bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-border resize-none font-mono leading-relaxed disabled:opacity-50"
            />
          </div>
          {error && <p className="text-xs text-error shrink-0">{error}</p>}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border shrink-0">
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
    </div>,
    document.body,
  );
}
