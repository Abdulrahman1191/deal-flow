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
  YES: "text-green-400 border-green-700",
  MAYBE: "text-yellow-400 border-yellow-700",
  REJECT: "text-red-400 border-red-700",
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className={`bg-gray-900 border ${BUCKET_TONE[bucket]} rounded-2xl w-full max-w-md shadow-2xl`}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-800">
          <p className="text-white font-semibold">{companyName}</p>
          <p className={`text-xs font-medium uppercase tracking-wider mt-0.5 ${BUCKET_TONE[bucket]}`}>
            {BUCKET_LABEL[bucket]}
          </p>
          <p className="text-[11px] text-gray-500 mt-2">
            Why this bucket? Helps train the AI to match your judgement.
            <span className="text-gray-600"> Optional — skip anytime.</span>
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
                return (
                  <button
                    key={tag}
                    onClick={() => toggle(tag)}
                    className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                      isOn
                        ? bucket === "YES"
                          ? "bg-green-500/20 text-green-200 border-green-600"
                          : bucket === "REJECT"
                            ? "bg-red-500/20 text-red-200 border-red-600"
                            : "bg-yellow-500/20 text-yellow-200 border-yellow-600"
                        : "bg-gray-800/50 text-gray-400 border-gray-700 hover:text-gray-200"
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
              placeholder="Anything specific to remember about this lead…"
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
              onClick={() =>
                onSubmit({ reason_tags: Array.from(selected), reason: note })
              }
              className="px-5 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
