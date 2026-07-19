import { useState } from "react";

interface Props {
  companyName: string;
  aiBucket: string;
  rating: "up" | "down";
  onSubmit: (data: { reason_tags: string[]; reason: string }) => void;
  onCancel: () => void;
}

// Tags depend on BOTH the bucket and the rating direction: thumbs-up = "why
// the bucket is right" (reinforce the pattern); thumbs-down = "why it's
// wrong" (correct it). A REJECT lead's 👍 reasons must not read like a YES
// lead's 👍 reasons, since agreeing with a rejection means the opposite signals.
const TAGS_YES_UP = [
  "Right bucket",
  "Strong founder–market fit",
  "Real tech moat / IP",
  "Big or growing market",
  "Good market timing",
  "Matches a winner pattern",
  "Other",
];
const TAGS_YES_DOWN = [
  "Wrong bucket — should pass",
  "Weak founder–market fit",
  "No real moat / commodity",
  "Market too small",
  "Team can't execute",
  "Overhyped / thin substance",
  "Other",
];
const TAGS_REJECT_UP = [
  "Right bucket",
  "Poor founder–market fit",
  "Not deep tech / wrong model",
  "Market too small / niche",
  "No moat / easily copied",
  "Wrong stage or bad timing",
  "Other",
];
const TAGS_REJECT_DOWN = [
  "Wrong bucket — should consider",
  "Strong founder overlooked",
  "Real tech / moat missed",
  "Big market missed",
  "Good timing missed",
  "Matches a winner pattern",
  "Other",
];
const TAGS_MAYBE_UP = [
  "Right bucket — genuinely borderline",
  "Unclear moat",
  "Needs more diligence",
  "Interesting but too early",
  "Mixed signals",
  "Other",
];
const TAGS_MAYBE_DOWN = [
  "Should be a clear YES",
  "Should be a clear REJECT",
  "Enough signal to decide",
  "Wrong bucket",
  "Other",
];

// Fallback for an empty/unrecognized bucket, so the modal never renders empty.
const TAGS_GENERIC_UP = ["Right bucket", "Other"];
const TAGS_GENERIC_DOWN = ["Wrong bucket", "Other"];

function getTags(aiBucket: string, rating: "up" | "down"): string[] {
  const bucket = aiBucket.trim().toLowerCase();
  const isUp = rating === "up";
  if (bucket === "yes") return isUp ? TAGS_YES_UP : TAGS_YES_DOWN;
  if (bucket === "reject") return isUp ? TAGS_REJECT_UP : TAGS_REJECT_DOWN;
  if (bucket === "maybe") return isUp ? TAGS_MAYBE_UP : TAGS_MAYBE_DOWN;
  return isUp ? TAGS_GENERIC_UP : TAGS_GENERIC_DOWN;
}

export default function FeedbackModal({
  companyName,
  aiBucket,
  rating,
  onSubmit,
  onCancel,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  const isUp = rating === "up";
  const tags = getTags(aiBucket, rating);
  const accent = isUp ? "green" : "orange";
  const hasFeedback = selected.size > 0 || note.trim().length > 0;

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
          <div className="flex items-center gap-2">
            {!hasFeedback && (
              <span className="text-[11px] text-muted-foreground">Pick a tag or add a note</span>
            )}
            <button
              onClick={() => onSubmit({ reason_tags: Array.from(selected), reason: note })}
              disabled={!hasFeedback}
              className={`px-5 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
                hasFeedback
                  ? "bg-primary hover:bg-primary/90"
                  : "bg-primary/40 cursor-not-allowed"
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
