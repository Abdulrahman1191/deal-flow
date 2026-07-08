import { useState } from "react";

interface Props {
  companyName: string;
  aiBucket: string;
  rating: "up" | "down";
  onSubmit: (data: { reason_tags: string[]; reason: string }) => void;
  onSkip: () => void;
  onCancel: () => void;
}

// Tags differ by direction. Thumbs-up = "what made this a good call" (so we can
// reinforce the pattern); thumbs-down = "what's off" (so we can correct it).
const TAGS_DOWN = [
  "Wrong bucket",
  "Missed a red flag",
  "Too harsh",
  "Too generous",
  "Weak research",
  "Wrong precedents",
  "Other",
];
const TAGS_UP = [
  "Right bucket",
  "Strong tech moat",
  "Right founder",
  "Good market timing",
  "Matches a winner pattern",
  "Good precedents",
  "Other",
];

export default function FeedbackModal({
  companyName,
  aiBucket,
  rating,
  onSubmit,
  onSkip,
  onCancel,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  const isUp = rating === "up";
  const tags = isUp ? TAGS_UP : TAGS_DOWN;
  const accent = isUp ? "green" : "orange";

  const toggle = (tag: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4 animate-fade-in">
      <div className={`bg-card border ${isUp ? "border-success" : "border-warning"} rounded-2xl w-full max-w-md shadow-2xl flex flex-col max-h-[90vh] animate-scale-in`}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-border">
          <p className="text-foreground font-semibold">{companyName}</p>
          <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${isUp ? "text-success" : "text-warning"}`}>
            {isUp
              ? `👍 AI said ${aiBucket} — what made it right?`
              : `👎 AI said ${aiBucket} — what's off?`}
          </p>
          <p className="text-[11px] text-muted-foreground mt-2">
            Your feedback trains the AI to match your judgement.
            <span className="text-muted-foreground"> Optional — skip anytime.</span>
            {!isUp && (
              <>
                <br />
                <span className="text-muted-foreground">
                  To change the bucket, use the YES / MAYBE / REJECT chips on the card.
                </span>
              </>
            )}
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
                const onCls = isUp
                  ? "bg-success/20 text-success border-success"
                  : "bg-warning/20 text-warning border-warning";
                return (
                  <button
                    key={tag}
                    onClick={() => toggle(tag)}
                    className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                      isOn ? onCls : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
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
              placeholder={isUp ? "What pattern should the AI keep doing?" : "What should the AI have caught?"}
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
              onClick={() => onSubmit({ reason_tags: Array.from(selected), reason: note })}
              className={`px-5 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
                "bg-primary hover:bg-primary/90"
              }`}
            >
              Submit feedback
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
