import { useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  describeUndoResult,
  fetchArchive,
  fetchLeadEvents,
  undoLastAction,
  type ArchiveItem,
  type ArchiveOutcomes,
  type LeadEvent,
} from "../api/leads";
import SortToggle, { type SortOrder } from "../components/shared/SortToggle";
import { useToast } from "../components/shared/Toast";
import useAppStore from "../store/useAppStore";

const SECTIONS: { key: ArchiveOutcomes; label: string; tone: string }[] = [
  { key: "sent_meeting_request", label: "Sent: Meeting Request", tone: "text-success" },
  { key: "sent_rejection",       label: "Sent: Rejection",       tone: "text-error" },
  { key: "no_reply",             label: "Archived: No Reply",    tone: "text-foreground" },
  { key: "sent_other",           label: "Sent: Other",           tone: "text-warning" },
  { key: "manual",               label: "Manually Archived",     tone: "text-muted-foreground" },
];

const EVENT_LABEL: Record<string, string> = {
  assessed: "AI assessed",
  bucket_overridden: "Bucket overridden",
  draft_approved: "Draft approved",
  email_sent: "Email sent",
  archived: "Archived",
  archived_no_reply: "Archived (no reply)",
  converted: "Converted to Opportunity",
  copper_updated: "Synced from Copper",
  action_undone: "Archive undone",
};

function EventRow({ event }: { event: LeadEvent }) {
  const label = EVENT_LABEL[event.event_type] ?? event.event_type;
  const detail = event.payload && Object.keys(event.payload).length
    ? JSON.stringify(event.payload)
    : null;
  return (
    <li className="flex items-start gap-3 py-1.5 text-xs">
      <span className="text-muted-foreground w-32 shrink-0">
        {new Date(event.created_at).toLocaleString("en-GB")}
      </span>
      <span className="text-foreground font-medium">{label}</span>
      {detail && <span className="text-muted-foreground truncate">— {detail}</span>}
    </li>
  );
}

function ArchiveRow({ item }: { item: ArchiveItem }) {
  const [expanded, setExpanded] = useState(false);
  const { data: events = [] } = useQuery({
    queryKey: ["lead-events", item.id],
    queryFn: () => fetchLeadEvents(item.id),
    enabled: expanded,
  });
  const qc = useQueryClient();
  const toast = useToast();
  // Admin "view as" QA mode (issue #52): the backend already 403s mutations
  // while impersonating (block_if_impersonating) — this just disables the
  // control in the UI so it appears read-only instead of failing on click.
  const readOnly = !!useAppStore((s) => s.viewAs);

  const restoreMutation = useMutation({
    mutationFn: () => undoLastAction(item.id),
    onSuccess: (data) => toast(describeUndoResult(data, item.company_name)),
    onError: (err: unknown) => {
      const detail =
        axios.isAxiosError(err) &&
        (err.response?.status === 404 || err.response?.status === 409)
          ? (err.response.data as { detail?: string } | undefined)?.detail
          : undefined;
      toast(detail ?? "Couldn't restore this lead — please try again.");
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["archive"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
  });

  return (
    <div
      className="bg-card border border-border rounded-lg"
      data-testid="archive-row"
    >
      <div className="w-full px-4 py-3 flex items-center justify-between gap-3 hover:bg-muted transition-colors">
        <button
          className="min-w-0 flex-1 text-left"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground text-sm truncate">
              {item.company_name}
            </span>
            {item.bucket && (
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {item.bucket}
              </span>
            )}
            {item.copper_opportunity_id && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success">
                Opportunity
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-xs text-muted-foreground">
              {new Date(item.archived_at).toLocaleString("en-GB")}
            </span>
            {item.company_linkedin_url && (
              <a
                href={item.company_linkedin_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="text-xs text-info hover:underline"
              >
                LinkedIn ↗
              </a>
            )}
          </div>
        </button>
        <button
          onClick={() => {
            if (confirm(`Restore ${item.company_name} to its original bucket?`)) {
              restoreMutation.mutate();
            }
          }}
          disabled={restoreMutation.isPending || readOnly}
          title={readOnly ? "Read-only while viewing another user's board" : undefined}
          data-testid="restore-archive-btn"
          className="shrink-0 text-xs px-2.5 py-1.5 rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {restoreMutation.isPending ? "Restoring…" : "Restore"}
        </button>
        <button
          className="shrink-0 text-xs text-muted-foreground px-1"
          onClick={() => setExpanded(!expanded)}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? "▲" : "▼"}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-border px-4 py-3">
          {events.length === 0 ? (
            <p className="text-xs text-muted-foreground">No events recorded.</p>
          ) : (
            <ul className="space-y-0.5">
              {events.map((e) => (
                <EventRow key={e.id} event={e} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function ArchivePage() {
  const [sort, setSort] = useState<SortOrder>("newest");
  const { data, isLoading } = useQuery({
    queryKey: ["archive", sort],
    queryFn: () => fetchArchive(sort),
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Loading archive…</p>;
  }
  if (!data) return null;

  const total = SECTIONS.reduce((sum, s) => sum + (data[s.key]?.length ?? 0), 0);

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Archive</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {total} archived {total === 1 ? "company" : "companies"}. Click a row to see its full activity timeline.
          </p>
        </div>
        <SortToggle value={sort} onChange={setSort} />
      </div>

      {SECTIONS.map((section) => {
        const items = data[section.key] ?? [];
        if (items.length === 0) return null;
        return (
          <section key={section.key}>
            <h2 className={`text-sm font-semibold mb-2 ${section.tone}`}>
              {section.label}{" "}
              <span className="text-muted-foreground font-normal">({items.length})</span>
            </h2>
            <div className="space-y-2">
              {items.map((item) => (
                <ArchiveRow key={item.id} item={item} />
              ))}
            </div>
          </section>
        );
      })}

      {total === 0 && (
        <div className="border border-dashed border-border rounded-2xl bg-card/50 py-16 px-6 text-center max-w-md mx-auto">
          <h3 className="font-heading text-base font-semibold text-foreground">Nothing archived yet</h3>
          <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
            Deals you skip or action from the board will show up here, grouped by outcome.
          </p>
        </div>
      )}
    </div>
  );
}
