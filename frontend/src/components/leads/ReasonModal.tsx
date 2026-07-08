import { useState } from "react";

type Bucket = "YES" | "MAYBE" | "REJECT";

interface Props {
  bucket: Bucket;
  companyName: string;
  onSubmit: (data: { reason_tags: string[]; reason: string }) => void;
  onSkip: () => void;
  onCancel: () => void;
}

// Tag vocabulary is intentionally bucket-specific. Showing "Strong tech moat"
// on a REJECT modal would be confusing; showing "Marketplace model" on a YES
// modal would be irrelevant. Tags are drawn from the rubric criteria so they
// map directly back to the scoring breakdown when we later use these in the
// LLM prompt.
const TAGS_BY_BUCKET: Record<Bucket, string[]> = {
  REJECT: [
    "Not MENA",
    "Marketplace model",
    "No deep tech",
    "Weak founder",
    "Wrong stage",
    "Off-thesis",
    "Already passed",
    "Other",
  ],
  YES: [
    "Strong tech moat",
    "Exceptional team",
    "Right MENA bet",
    "Known founder",
    "Hot market window",
    "Other",
  ],
  MAYBE: [
    "Needs more info",
    "Borderline thesis fit",
    "Worth a quick call",
    "Founder-driven, model unclear",
    "Other",
  ],
};

const BUCKET_TONE: Record<Bucket, string> = {
  YES: "text-success border-success",
  MAYBE: "text-warning border-warning",
  REJECT: "text-error border-error",
};

const BUCKET_LABEL: Record<Bucket, string> = {
  YES: "YES — Schedule Meeting",
  MAYBE: "MAYBE — Review",
  REJECT: "REJECT",
};

export default function ReasonModal({
  bucket,
  companyName,
  onSubmit,
  onSkip,
  onCancel,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  const tags = TAGS_BY_BUCKET[bucket];

  const toggle = (tag: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in">
      <div className={`bg-card border ${BUCKET_TONE[bucket]} rounded-2xl w-full max-w-md shadow-2xl flex flex-col max-h-[90vh] animate-scale-in`}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-border">
          <p className="text-foreground font-semibold">{companyName}</p>
          <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${BUCKET_TONE[bucket]}`}>
            {BUCKET_LABEL[bucket]}
          </p>
          <p className="text-[11px] text-muted-foreground mt-2">
            Why this bucket? Helps train the AI to match your judgement.
            <span className="text-muted-foreground"> Optional — skip anytime.</span>
          </p>
        </div>

        {/* Tags */}
        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-2">
              Quick reasons (tap any that apply)
            </label>
            <div className="flex flex-wrap gap-2">
              {tags.map((tag) => {
                const isOn = selected.has(tag);
                return (
                  <button
                    key={tag}
                    onClick={() => toggle(tag)}
                    className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                      isOn
                        ? bucket === "YES"
                          ? "bg-success/20 text-success border-success"
                          : bucket === "REJECT"
                            ? "bg-error/20 text-error border-error"
                            : "bg-warning/20 text-warning border-warning"
                        : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
                    }`}
                  >
                    {tag}
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
              placeholder="Anything specific to remember about this lead…"
              className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-border resize-none"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border">
          <button
            onClick={onCancel}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Cancel
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={onSkip}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-2"
            >
              Skip
            </button>
            <button
              onClick={() =>
                onSubmit({ reason_tags: Array.from(selected), reason: note })
              }
              className="px-5 py-2 text-sm font-medium rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
