import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onApprove: () => void;
  onReassess: () => void;
  reassessing?: boolean;
}

export default function ActionButtons({ lead, onApprove, onReassess, reassessing = false }: Props) {
  const { assessment } = lead;
  const sent = !!assessment?.sent_at;
  const approved = !!assessment?.approved_at && !sent;

  if (sent) {
    return (
      <span className="text-xs text-muted-foreground">
        Sent {new Date(assessment!.sent_at!).toLocaleDateString("en-GB")}
      </span>
    );
  }

  if (approved) {
    return (
      <span className="text-xs text-success font-medium">
        Approved — in Send Queue
      </span>
    );
  }

  const effectiveBucket = assessment?.user_override ?? assessment?.bucket;
  // Enforced learning: a lead can't be approved/sent until it's been rated
  // (👍/👎). Mirrors the backend gate on /approve, /send, /mark-sent.
  const rated = !!assessment?.user_rating;
  const needsRating = !rated && (effectiveBucket === "YES" || effectiveBucket === "REJECT");

  return (
    <div className="flex gap-2 flex-wrap items-center">
      {effectiveBucket === "REJECT" && (
        <button
          onClick={onApprove}
          disabled={!rated}
          title={rated ? undefined : "Rate the recommendation (👍/👎) first"}
          className="px-3 py-1.5 text-xs rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Approve Email
        </button>
      )}
      {effectiveBucket === "YES" && (
        <button
          onClick={onApprove}
          disabled={!rated}
          title={rated ? undefined : "Rate the recommendation (👍/👎) first"}
          className="px-3 py-1.5 text-xs rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Approve Meeting Request
        </button>
      )}
      {effectiveBucket === "MAYBE" && (
        <span className="text-xs text-warning py-1.5">Flagged for review</span>
      )}
      {needsRating && (
        <span className="text-[10px] text-muted-foreground" data-testid="rating-required-hint">
          Rate 👍/👎 to enable
        </span>
      )}
      <button
        onClick={onReassess}
        disabled={reassessing}
        className="px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {reassessing ? "Reassessing…" : "Reassess"}
      </button>
    </div>
  );
}
