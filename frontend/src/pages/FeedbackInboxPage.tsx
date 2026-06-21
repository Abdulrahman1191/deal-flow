import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchFeedback, toggleFeedbackResolved, type FeedbackItem } from "../api/feedback";

function categoryTone(c: string | null) {
  if (c === "Bug") return "text-error bg-error/10";
  if (c === "Suggestion") return "text-info bg-primary/10";
  if (c === "UX") return "text-primary bg-primary/10";
  return "text-foreground bg-muted/30";
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
      className={`bg-card border border-border rounded-xl p-4 space-y-2 ${
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
          <span className="text-xs text-muted-foreground">{item.user_email}</span>
          <span className="text-xs text-muted-foreground">
            {new Date(item.created_at).toLocaleString("en-GB")}
          </span>
        </div>
        <button
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
        >
          {resolved ? "Reopen" : "Mark resolved"}
        </button>
      </div>
      <p className={`text-sm whitespace-pre-wrap leading-relaxed ${resolved ? "text-muted-foreground line-through" : "text-foreground"}`}>
        {item.message}
      </p>
      {item.page_url && (
        <a
          href={item.page_url}
          target="_blank"
          rel="noreferrer"
          className="text-[11px] text-info hover:underline truncate block"
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

  if (isLoading) return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Loading feedback…</p>;
  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Feedback inbox is only visible to the owner.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load feedback.</p>;
  }

  const items = data ?? [];
  const open = items.filter((i) => !i.resolved_at);
  const done = items.filter((i) => i.resolved_at);

  return (
    <div className="p-4 sm:p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Feedback Inbox</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {open.length} open · {done.length} resolved
        </p>
      </div>

      {items.length === 0 && (
        <p className="text-sm text-muted-foreground">No feedback yet.</p>
      )}

      {open.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">Open</h2>
          {open.map((i) => <Row key={i.id} item={i} />)}
        </section>
      )}

      {done.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-muted-foreground">Resolved</h2>
          {done.map((i) => <Row key={i.id} item={i} />)}
        </section>
      )}
    </div>
  );
}
