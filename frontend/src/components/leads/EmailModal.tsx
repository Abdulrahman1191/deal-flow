import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { regenerateDraft, sendEmail, updateDraft } from "../../api/assessments";
import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onClose: () => void;
}

export default function EmailModal({ lead, onClose }: Props) {
  const { assessment } = lead;
  const [subject, setSubject] = useState(assessment?.draft_subject ?? "");
  const [body, setBody] = useState(assessment?.draft_body ?? "");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const regenMutation = useMutation({
    mutationFn: () => regenerateDraft(lead.id),
    onSuccess: (data) => {
      setSubject(data.draft_subject ?? "");
      setBody(data.draft_body ?? "");
      setError(null);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(msg ?? "Couldn't regenerate draft — write it manually below.");
    },
  });

  // Auto-regenerate when modal opens with no draft body (e.g. silent regen failure)
  useEffect(() => {
    if (!assessment?.draft_body && !regenMutation.isPending && !regenMutation.isError) {
      regenMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(msg ?? "Failed to send — try again.");
    },
  });

  const effectiveBucket = assessment?.user_override ?? assessment?.bucket;
  const bucketColor = effectiveBucket === "YES" ? "text-success" : "text-error";
  const headerLabel = (() => {
    const dt = assessment?.draft_type;
    if (dt === "meeting_request") return "Meeting Request";
    if (dt === "rejection") return "Rejection";
    return effectiveBucket === "YES" ? "Meeting Request" : "Rejection";
  })();

  const generating = regenMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
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
              className="text-xs text-info hover:text-info transition-colors disabled:opacity-50"
              title="Ask the AI to rewrite this draft"
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
          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1">
              Subject
            </label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={generating}
              className={`w-full bg-background border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-border disabled:opacity-50 ${!subject.trim() && !generating ? "border-error" : "border-border"}`}
            />
            {!subject.trim() && !generating && (
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
              disabled={generating}
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
            disabled={sendMutation.isPending || generating || !body.trim() || !subject.trim()}
            className="px-5 py-2 text-sm font-medium rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {sendMutation.isPending ? "Sending…" : "Send Email"}
          </button>
        </div>
      </div>
    </div>
  );
}
