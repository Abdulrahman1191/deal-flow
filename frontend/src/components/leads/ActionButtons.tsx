import type { Lead } from "../../types/lead";

interface Props {
  lead: Lead;
  onApprove: () => void;
  onReassess: () => void;
  onArchiveNoReply: () => void;
  reassessing?: boolean;
  archiving?: boolean;
  /** Admin "view as" QA mode (issue #52) — disables all mutating controls. */
  readOnly?: boolean;
}

export default function ActionButtons({
  lead,
  onApprove,
  onReassess,
  onArchiveNoReply,
  reassessing = false,
  archiving = false,
  readOnly = false,
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
  // Enforced learning: a lead can't be approved/sent until it's been rated
  // (👍/👎). Mirrors the backend gate on /approve, /send, /mark-sent, and
  // /archive-no-reply. A bucket override alone does NOT satisfy this.
  const rated = !!assessment?.user_rating;
  const needsRating = !rated;
  const gateTitle = readOnly
    ? "Read-only while viewing another user's board"
    : needsRating
      ? "Rate the AI's call (👍/👎) above first"
      : undefined;
  // Once rated, the primary action is the one obvious next click — give it
  // a stronger visual pull than the secondary buttons next to it.
  const primaryClasses = needsRating
    ? "bg-primary hover:bg-primary/90 text-white disabled:opacity-40 disabled:cursor-not-allowed"
    : "bg-primary hover:bg-primary/90 text-white shadow-sm ring-2 ring-primary/30 disabled:opacity-40 disabled:cursor-not-allowed disabled:ring-0 disabled:shadow-none";

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {needsRating ? "2. Approve / Send / Archive" : "Next step"}
      </span>
      <div className="flex gap-2 flex-wrap items-center">
        {effectiveBucket === "REJECT" && (
          <button
            onClick={onApprove}
            disabled={!rated || readOnly}
            title={gateTitle}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${primaryClasses}`}
          >
            Approve Email
          </button>
        )}
        {effectiveBucket === "YES" && (
          <button
            onClick={onApprove}
            disabled={!rated || readOnly}
            title={gateTitle}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${primaryClasses}`}
          >
            Approve Meeting Request
          </button>
        )}
        {effectiveBucket === "MAYBE" && (
          <span className="text-xs text-warning py-1.5">Flagged for review</span>
        )}
        <button
          onClick={onArchiveNoReply}
          disabled={!rated || archiving || readOnly}
          title={gateTitle}
          data-testid="archive-no-reply-btn"
          className="px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {archiving ? "Archiving…" : "Archive (no email)"}
        </button>
        <button
          onClick={onReassess}
          disabled={reassessing || readOnly}
          title={readOnly ? "Read-only while viewing another user's board" : undefined}
          className="px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {reassessing ? "Reassessing…" : "Reassess"}
        </button>
      </div>
    </div>
  );
}
