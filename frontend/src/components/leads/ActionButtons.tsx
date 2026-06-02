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
      <span className="text-xs text-gray-500">
        Sent {new Date(assessment!.sent_at!).toLocaleDateString("en-GB")}
      </span>
    );
  }

  if (approved) {
    return (
      <span className="text-xs text-green-600 font-medium">
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
          className="px-3 py-1.5 text-xs rounded-lg bg-red-700 hover:bg-red-600 text-white transition-colors"
        >
          Approve Email
        </button>
      )}
      {effectiveBucket === "YES" && (
        <button
          onClick={onApprove}
          className="px-3 py-1.5 text-xs rounded-lg bg-green-700 hover:bg-green-600 text-white transition-colors"
        >
          Approve Meeting Request
        </button>
      )}
      {effectiveBucket === "MAYBE" && (
        <span className="text-xs text-yellow-400 py-1.5">Flagged for review</span>
      )}
      <button
        onClick={onReassess}
        disabled={reassessing}
        className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {reassessing ? "Reassessing…" : "Reassess"}
      </button>
    </div>
  );
}
