import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchSendQueue, markSent, type SendQueueItem } from "../api/assessments";
import Badge from "../components/shared/Badge";
import { format } from "date-fns";

function SendQueueCard({ item }: { item: SendQueueItem }) {
  const qc = useQueryClient();
  const markSentMutation = useMutation({
    mutationFn: () => markSent(item.lead_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["send-queue"] }),
  });

  return (
    <div
      data-testid="send-queue-item"
      className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-4"
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-semibold text-white text-sm">{item.company_name}</p>
          <p className="text-xs text-gray-500 mt-0.5">
            Approved {format(new Date(item.approved_at), "dd/MM/yyyy · HH:mm")}
          </p>
        </div>
        <Badge
          label={item.draft_type === "rejection" ? "Rejection" : "Meeting Request"}
          variant={item.draft_type === "rejection" ? "reject" : "yes"}
        />
      </div>

      {/* Recipient */}
      <div>
        <p className="text-xs text-gray-500 mb-1 uppercase tracking-wider">To</p>
        <p
          data-testid="recipient-email"
          className="text-sm text-white font-mono bg-gray-800 px-3 py-2 rounded-lg"
        >
          {item.recipient_email || "— no email on record —"}
        </p>
      </div>

      {/* Subject */}
      <div>
        <p className="text-xs text-gray-500 mb-1 uppercase tracking-wider">Subject</p>
        <p
          data-testid="email-subject"
          className="text-sm text-white bg-gray-800 px-3 py-2 rounded-lg"
        >
          {item.draft_subject || "—"}
        </p>
      </div>

      {/* Body */}
      <div>
        <p className="text-xs text-gray-500 mb-1 uppercase tracking-wider">Body</p>
        <pre
          data-testid="email-body"
          className="text-sm text-gray-200 bg-gray-800 px-4 py-3 rounded-lg whitespace-pre-wrap font-sans leading-relaxed"
        >
          {item.draft_body || "—"}
        </pre>
      </div>

      {/* Action */}
      <div className="flex justify-end pt-1">
        <button
          data-testid="mark-sent-btn"
          onClick={() => markSentMutation.mutate()}
          disabled={markSentMutation.isPending || !item.recipient_email}
          title={!item.recipient_email ? "Add an email address in Copper first" : undefined}
          className="px-5 py-2 text-sm rounded-lg bg-green-700 hover:bg-green-600 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
        >
          {markSentMutation.isPending ? "Marking…" : "Mark as Sent"}
        </button>
      </div>

      {markSentMutation.isError && (
        <p className="text-xs text-red-400 text-right">Failed — try again.</p>
      )}
    </div>
  );
}

export default function SendQueuePage() {
  const { data: items = [], isLoading } = useQuery({
    queryKey: ["send-queue"],
    queryFn: fetchSendQueue,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return <p className="text-gray-500 text-sm p-6 animate-pulse">Loading send queue…</p>;
  }

  return (
    <div className="p-6 max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-white">Send Queue</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            Approved drafts waiting to be sent via Gmail
          </p>
        </div>
        <span className="text-xs text-gray-500">{items.length} pending</span>
      </div>

      {items.length === 0 && (
        <div className="text-center py-16 text-gray-600 text-sm border border-dashed border-gray-800 rounded-xl">
          No emails pending — all caught up.
        </div>
      )}

      {items.map((item) => (
        <SendQueueCard key={item.lead_id} item={item} />
      ))}
    </div>
  );
}
