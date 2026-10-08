import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { applyRejectionTemplate, regenerateDraft, sendEmail, updateDraft } from "../../api/assessments";
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

// Mirrors backend MAX_REJECTION_REASONS (schemas/assessment.py).
const MAX_REJECTION_REASONS = 3;

// The shareable (non-INTERNAL_ONLY_REASONS) canonical labels from
// claude_agent.UNQUAL_REASON_OPTIONS, grouped by which rejection_templates
// key they select (issue #232) -- shown as one-click chips so the partner
// never has to type a reason to pick a template.
const REJECTION_REASON_OPTIONS: { label: string; template: string }[] = [
  { label: "Conflict of interest", template: "CONFLICT" },
  { label: "Market size", template: "MARKET_SIZE" },
  { label: "Exit potential", template: "MARKET_SIZE" },
  { label: "Lack of traction", template: "TRACTION" },
  { label: "Business Model", template: "TRACTION" },
  { label: "Out of our stage", template: "MANDATE" },
  { label: "Out of our region", template: "MANDATE" },
  { label: "Regulations and Legislation", template: "MANDATE" },
  { label: "Technology and IP", template: "MANDATE" },
  { label: "Other", template: "MANDATE" },
];
const KNOWN_REASON_LABELS = new Set(REJECTION_REASON_OPTIONS.map((o) => o.label));

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

  // Reason chips + EN/AR toggle for the rejection_templates picker (issue
  // #232) — only meaningful for REJECT. Seeded from whatever's already
  // recorded on the card; internal-only labels (never shown as chips here)
  // are dropped rather than crashing on an unknown chip.
  const [selectedReasons, setSelectedReasons] = useState<Set<string>>(
    () => new Set((assessment?.rejection_reasons ?? []).filter((r) => KNOWN_REASON_LABELS.has(r))),
  );
  const [language, setLanguage] = useState<"en" | "ar">(assessment?.rejection_language ?? "en");

  // Signature of the draft currently reflected in the fields above, so we can
  // tell "the draft changed under us" (a bucket override or background
  // reassessment landing while this modal is open) apart from the user's own
  // edits, and re-sync instead of silently leaving stale text on screen.
  const loadedSignature = useRef(`${assessment?.draft_type}:${assessment?.draft_body}`);
  // Ensures the REJECT auto-apply-on-open effect below fires exactly once
  // per mount, instead of re-firing on every render.
  const autoAppliedTemplateRef = useRef(false);

  const regenMutation = useMutation({
    mutationFn: () => regenerateDraft(lead.id, Array.from(selectedReasons)),
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

  // Renders a fixed rejection_templates draft — zero LLM calls (issue #232).
  // This is the DEFAULT way a REJECT draft gets (re)written; "Regenerate
  // with AI" above stays as the explicit opt-in for bespoke wording.
  const templateMutation = useMutation({
    mutationFn: (vars: { reasons: string[]; language: "en" | "ar" | null }) =>
      applyRejectionTemplate(lead.id, vars),
    onSuccess: (data) => {
      loadedSignature.current = `${data.draft_type}:${data.draft_body}`;
      setSubject(data.draft_subject ?? "");
      setBody(data.draft_body ?? "");
      if (data.rejection_language) setLanguage(data.rejection_language);
      setError(null);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (err: unknown) => {
      setError(extractDetail(err) ?? "Couldn't apply the rejection template — try again.");
    },
  });

  const toggleReason = (label: string) => {
    const next = new Set(selectedReasons);
    if (next.has(label)) {
      next.delete(label);
    } else if (next.size < MAX_REJECTION_REASONS) {
      next.add(label);
    } else {
      return;
    }
    setSelectedReasons(next);
    templateMutation.mutate({ reasons: Array.from(next), language });
  };

  const handleLanguageChange = (lng: "en" | "ar") => {
    setLanguage(lng);
    templateMutation.mutate({ reasons: Array.from(selectedReasons), language: lng });
  };

  // Auto-regenerate via the LLM whenever the modal is showing a stale YES
  // draft: on open with a missing/mismatched draft (e.g. a silent regen
  // failure), and again if the effective bucket changes out from under an
  // already-open modal. REJECT has its own template-based effect below;
  // MAYBE never has an email to write.
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
    if (effectiveBucket === "YES" && isStale && !regenMutation.isPending) {
      regenMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessment?.draft_type, assessment?.draft_body, effectiveBucket]);

  // Template is the DEFAULT for a REJECT draft (issue #232): apply it once
  // on open rather than trusting whatever's cached (which may be an older
  // LLM-written draft) — the partner can still edit freely afterward, or
  // fall back to "Regenerate with AI".
  useEffect(() => {
    if (effectiveBucket === "REJECT" && !autoAppliedTemplateRef.current && !templateMutation.isPending) {
      autoAppliedTemplateRef.current = true;
      templateMutation.mutate({ reasons: Array.from(selectedReasons), language: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveBucket]);

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

  const applyingTemplate = templateMutation.isPending;
  const generating = regenMutation.isPending || applyingTemplate;
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
            {regenMutation.isPending && (
              <p className="text-[10px] text-info mt-1 animate-pulse">
                AI is writing the draft…
              </p>
            )}
            {applyingTemplate && (
              <p className="text-[10px] text-muted-foreground mt-1 animate-pulse">
                Applying template…
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
              title="Ask the AI to rewrite this draft (bespoke wording, not the fixed template)"
              data-testid="regenerate-draft-btn"
            >
              {regenMutation.isPending ? "…" : "Regenerate with AI ↻"}
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
          {effectiveBucket === "REJECT" && (
            <div
              className="space-y-2 border border-border rounded-lg px-3 py-2.5 bg-muted/20"
              data-testid="rejection-template-picker"
            >
              <div className="flex items-center justify-between">
                <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Rejection reason (picks the template)
                </label>
                <div className="flex items-center gap-1 text-[10px]">
                  {(["en", "ar"] as const).map((lng) => (
                    <button
                      key={lng}
                      type="button"
                      onClick={() => handleLanguageChange(lng)}
                      disabled={applyingTemplate}
                      data-testid={`rejection-language-${lng}`}
                      className={`px-2 py-0.5 rounded-full border transition-colors disabled:opacity-50 ${
                        language === lng
                          ? "bg-primary text-primary-foreground border-primary"
                          : "text-muted-foreground border-border hover:text-foreground"
                      }`}
                    >
                      {lng.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {REJECTION_REASON_OPTIONS.map(({ label }) => {
                  const isOn = selectedReasons.has(label);
                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() => toggleReason(label)}
                      disabled={applyingTemplate}
                      className={`text-xs px-2.5 py-1 rounded-full border transition-colors disabled:opacity-50 ${
                        isOn
                          ? "bg-error/20 text-error border-error"
                          : "bg-background text-muted-foreground border-border hover:text-foreground"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] text-muted-foreground">
                No reason selected reads as a generic "outside our mandate" pass. Pick up to{" "}
                {MAX_REJECTION_REASONS}.
              </p>
            </div>
          )}
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
