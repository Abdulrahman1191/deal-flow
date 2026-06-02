import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchFeedback, toggleFeedbackResolved, type FeedbackItem } from "../api/feedback";

function categoryTone(c: string | null) {
  if (c === "Bug") return "text-red-300 bg-red-500/10";
  if (c === "Suggestion") return "text-blue-300 bg-blue-500/10";
  if (c === "UX") return "text-purple-300 bg-purple-500/10";
  return "text-gray-300 bg-gray-700/30";
}

function Row({ item }: { item: FeedbackItem }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => toggleFeedbackResolved(item.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["feedback"] }),
  });

  const resolved = !!item.resolved_at;

  return (
    <div
      className={`bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-2 ${
        resolved ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${categoryTone(item.category)}`}
          >
            {item.category ?? "Note"}
          </span>
          <span className="text-xs text-gray-400">{item.user_email}</span>
          <span className="text-xs text-gray-600">
            {new Date(item.created_at).toLocaleString("en-GB")}
          </span>
        </div>
        <button
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors disabled:opacity-50"
        >
          {resolved ? "Reopen" : "Mark resolved"}
        </button>
      </div>
      <p className={`text-sm whitespace-pre-wrap leading-relaxed ${resolved ? "text-gray-500 line-through" : "text-gray-100"}`}>
        {item.message}
      </p>
      {item.page_url && (
        <a
          href={item.page_url}
          target="_blank"
          rel="noreferrer"
          className="text-[11px] text-blue-400 hover:underline truncate block"
        >
          {item.page_url}
        </a>
      )}
    </div>
  );
}

export default function FeedbackInboxPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["feedback"],
    queryFn: fetchFeedback,
    refetchInterval: 30_000,
  });

  if (isLoading) return <p className="p-6 text-sm text-gray-500">Loading feedback…</p>;
  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-6 text-sm text-gray-500">Feedback inbox is only visible to the owner.</p>;
    }
    return <p className="p-6 text-sm text-red-400">Failed to load feedback.</p>;
  }

  const items = data ?? [];
  const open = items.filter((i) => !i.resolved_at);
  const done = items.filter((i) => i.resolved_at);

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Feedback Inbox</h1>
        <p className="text-sm text-gray-500 mt-1">
          {open.length} open · {done.length} resolved
        </p>
      </div>

      {items.length === 0 && (
        <p className="text-sm text-gray-500">No feedback yet.</p>
      )}

      {open.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-gray-300">Open</h2>
          {open.map((i) => <Row key={i.id} item={i} />)}
        </section>
      )}

      {done.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-gray-500">Resolved</h2>
          {done.map((i) => <Row key={i.id} item={i} />)}
        </section>
      )}
    </div>
  );
}
