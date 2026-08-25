import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchMyReasons, type MyReasons } from "../../api/overrides";
import LearnedReasonChips from "./LearnedReasonChips";

type Bucket = "YES" | "MAYBE" | "REJECT";

interface Props {
  bucket: Bucket;
  companyName: string;
  onSubmit: (data: { reason_tags: string[]; reason: string }) => void;
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

// Which learned-reasons group (issue #152) backs each bucket. Moving a lead
// to YES/REJECT is also the decision point behind "Approve Meeting Request"
// / "Archive" downstream, so these double as the approve-send / archive-reject
// chip sets the issue asks for — there's no separate reason-capture step for
// those actions today.
const LEARNED_REASONS_KEY: Record<Bucket, keyof MyReasons> = {
  YES: "bucket_yes",
  MAYBE: "bucket_maybe",
  REJECT: "bucket_reject",
};

export default function ReasonModal({
  bucket,
  companyName,
  onSubmit,
  onCancel,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedLearned, setSelectedLearned] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  const tags = TAGS_BY_BUCKET[bucket];
  const hasReason = selected.size > 0 || note.trim().length > 0 || selectedLearned.size > 0;

  const { data: myReasons } = useQuery({ queryKey: ["my-reasons"], queryFn: fetchMyReasons, staleTime: 5 * 60 * 1000 });
  const learnedReasons = myReasons?.[LEARNED_REASONS_KEY[bucket]] ?? [];

  const toggle = (tag: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });

  const toggleLearned = (text: string) =>
    setSelectedLearned((prev) => {
      const next = new Set(prev);
      if (next.has(text)) next.delete(text);
      else next.add(text);
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

          <LearnedReasonChips
            reasons={learnedReasons}
            selected={selectedLearned}
            onToggle={toggleLearned}
            activeClassName={
              bucket === "YES"
                ? "bg-success/20 text-success border-success"
                : bucket === "REJECT"
                  ? "bg-error/20 text-error border-error"
                  : "bg-warning/20 text-warning border-warning"
            }
          />

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
          <div className="flex items-center gap-2">
            {!hasReason && (
              <span className="text-[11px] text-muted-foreground">Pick a tag or add a note</span>
            )}
            <button
              onClick={() =>
                onSubmit({
                  reason_tags: Array.from(selected),
                  reason: [note.trim(), ...Array.from(selectedLearned)].filter(Boolean).join("; "),
                })
              }
              disabled={!hasReason}
              className={`px-5 py-2 text-sm font-medium rounded-lg text-white transition-colors ${
                hasReason
                  ? "bg-primary hover:bg-primary/90"
                  : "bg-primary/40 cursor-not-allowed"
              }`}
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
