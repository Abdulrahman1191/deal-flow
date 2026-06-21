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

  return (
    <div className="flex gap-2 flex-wrap">
      {effectiveBucket === "REJECT" && (
        <button
          onClick={onApprove}
          className="px-3 py-1.5 text-xs rounded-lg bg-error hover:bg-error/90 text-white transition-colors"
        >
          Approve Email
        </button>
      )}
      {effectiveBucket === "YES" && (
        <button
          onClick={onApprove}
          className="px-3 py-1.5 text-xs rounded-lg bg-success hover:bg-success/90 text-white transition-colors"
        >
          Approve Meeting Request
        </button>
      )}
      {effectiveBucket === "MAYBE" && (
        <span className="text-xs text-warning py-1.5">Flagged for review</span>
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
