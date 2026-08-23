import { useState } from "react";
import { UNQUALIFICATION_REASONS } from "../../constants/unqualificationReasons";

interface Props {
  count: number;
  onSubmit: (data: { reason_option_ids: number[]; note: string }) => void;
  onCancel: () => void;
  submitting?: boolean;
}

/**
 * Bulk-archive confirm dialog (issue #141 follow-up). Unlike ReasonModal
 * (single-lead bucket override, where a tag OR a note is enough), a reason
 * here is mandatory -- every bulk-archived lead must land in Copper with an
 * Unqualification Reason, so Archive stays disabled until at least one chip
 * is picked. The note is optional and, when given, takes precedence over the
 * per-lead AI-generated detail on the backend.
 */
export default function BulkArchiveReasonModal({ count, onSubmit, onCancel, submitting = false }: Props) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [note, setNote] = useState("");

  const hasReason = selected.size > 0;

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-card border border-error rounded-2xl w-full max-w-md shadow-2xl flex flex-col max-h-[90vh] animate-scale-in">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border">
          <p className="text-foreground font-semibold">
            Archive {count} lead{count === 1 ? "" : "s"}
          </p>
          <p className="text-[11px] text-muted-foreground mt-2">
            Each lead is also marked Unqualified in Copper with the reason(s) chosen below.
          </p>
        </div>

        {/* Reasons */}
        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-2">
              Unqualification reason (required — applies to all selected)
            </label>
            <div className="flex flex-wrap gap-2">
              {UNQUALIFICATION_REASONS.map(({ label, id }) => {
                const isOn = selected.has(id);
                return (
                  <button
                    key={id}
                    onClick={() => toggle(id)}
                    className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                      isOn
                        ? "bg-error/20 text-error border-error"
                        : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1">
              Optional note
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="Applies to every selected lead's Copper detail field…"
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-border resize-none"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <div className="flex items-center gap-2">
            {!hasReason && (
              <span className="text-[11px] text-muted-foreground">Pick at least one reason</span>
            )}
            <button
              onClick={() =>
                onSubmit({ reason_option_ids: Array.from(selected), note: note.trim() })
              }
              disabled={!hasReason || submitting}
              className={`px-5 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
                hasReason && !submitting
                  ? "bg-error hover:bg-error/90"
                  : "bg-error/40 cursor-not-allowed"
              }`}
            >
              {submitting ? "Archiving…" : `Archive ${count}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
