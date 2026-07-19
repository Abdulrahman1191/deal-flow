import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onApprove: () => void;
  onArchive: () => void;
  onReassess: () => void;
  reassessing?: boolean;
  archiving?: boolean;
}

export default function ActionButtons({
  lead,
  onApprove,
  onArchive,
  onReassess,
  reassessing = false,
  archiving = false,
}: Props) {
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
  // Enforced learning: a lead can't be approved/sent/archived until it's been
  // rated (👍/👎). Mirrors the backend gate on /approve, /send, /archive-no-reply.
  const rated = !!assessment?.user_rating;
  const needsRating = !rated && (effectiveBucket === "YES" || effectiveBucket === "REJECT");
  const disposeDisabled = !rated || archiving;
  const ratingHint = rated ? undefined : "Rate the recommendation (👍/👎) first";

  const archiveButton = (
    <button
      onClick={onArchive}
      disabled={disposeDisabled}
      title={ratingHint}
      className="px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {archiving ? "Archiving…" : "Archive (no email)"}
    </button>
  );

  return (
    <div className="flex gap-2 flex-wrap items-center">
      {effectiveBucket === "REJECT" && (
        <div className="flex items-center gap-2" data-testid="reject-disposition-options">
          <button
            onClick={onApprove}
            disabled={!rated}
            title={ratingHint}
            className="px-3 py-1.5 text-xs rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Send rejection email
          </button>
          <span className="text-[10px] text-muted-foreground">or</span>
          {archiveButton}
        </div>
      )}
      {effectiveBucket === "YES" && (
        <>
          <button
            onClick={onApprove}
            disabled={!rated}
            title={ratingHint}
            className="px-3 py-1.5 text-xs rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Approve Meeting Request
          </button>
          {archiveButton}
        </>
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
