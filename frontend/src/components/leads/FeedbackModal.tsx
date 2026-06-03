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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className={`bg-gray-900 border ${isUp ? "border-green-700" : "border-orange-700"} rounded-2xl w-full max-w-md shadow-2xl`}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-800">
          <p className="text-white font-semibold">{companyName}</p>
          <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${isUp ? "text-green-400" : "text-orange-400"}`}>
            {isUp
              ? `👍 AI said ${aiBucket} — what made it right?`
              : `👎 AI said ${aiBucket} — what's off?`}
          </p>
          <p className="text-[11px] text-gray-500 mt-2">
            Your feedback trains the AI to match your judgement.
            <span className="text-gray-600"> Optional — skip anytime.</span>
            {!isUp && (
              <>
                <br />
                <span className="text-gray-600">
                  To change the bucket, use the YES / MAYBE / REJECT chips on the card.
                </span>
              </>
            )}
          </p>
        </div>

        {/* Tags */}
        <div className="px-5 py-4 space-y-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-gray-500 block mb-2">
              Quick reasons (tap any that apply)
            </label>
            <div className="flex flex-wrap gap-2">
              {tags.map((tag) => {
                const isOn = selected.has(tag);
                const onCls = isUp
                  ? "bg-green-500/20 text-green-200 border-green-600"
                  : "bg-orange-500/20 text-orange-200 border-orange-600";
                return (
                  <button
                    key={tag}
                    onClick={() => toggle(tag)}
                    className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                      isOn ? onCls : "bg-gray-800/50 text-gray-400 border-gray-700 hover:text-gray-200"
                    }`}
                  >
                    {tag}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-gray-500 block mb-1">
              Optional note
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder={isUp ? "What pattern should the AI keep doing?" : "What should the AI have caught?"}
              className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder:text-gray-700 focus:outline-none focus:border-gray-600 resize-none"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-gray-800">
          <button
            onClick={onCancel}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            Cancel
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={onSkip}
              className="text-xs text-gray-400 hover:text-gray-200 transition-colors px-3 py-2"
            >
              Skip
            </button>
            <button
              onClick={() => onSubmit({ reason_tags: Array.from(selected), reason: note })}
              className={`px-5 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
                isUp ? "bg-green-600 hover:bg-green-500" : "bg-orange-600 hover:bg-orange-500"
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
