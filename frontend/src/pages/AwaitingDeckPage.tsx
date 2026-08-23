import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { bulkArchiveLeads, fetchLeads, syncPitchDeck } from "../api/leads";
import { reassess } from "../api/assessments";
import useAppStore from "../store/useAppStore";
import { useBulkSelection } from "../hooks/useBulkSelection";
import Badge from "../components/shared/Badge";
import PriorContactChip from "../components/shared/PriorContactChip";
import SelectCheckbox from "../components/shared/SelectCheckbox";
import SortToggle, { type SortOrder } from "../components/shared/SortToggle";
import { useToast } from "../components/shared/Toast";
import BulkArchiveBar from "../components/leads/BulkArchiveBar";
import type { Lead } from "../types/lead";

// Shared with Navbar's count badge so both stay on the same cache entry.
export const AWAITING_DECK_QUERY_KEY = ["leads", "awaiting_deck"];

function SkeletonRow() {
  return (
    <div className="bg-card border border-border rounded-2xl p-5 animate-pulse space-y-3">
      <div className="h-3.5 w-40 rounded bg-muted" />
      <div className="h-2.5 w-24 rounded bg-muted" />
      <div className="h-8 w-48 rounded-lg bg-muted" />
    </div>
  );
}

function AwaitingDeckCard({
  lead,
  selected,
  onToggleSelect,
}: {
  lead: Lead;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  const qc = useQueryClient();
  const readOnly = !!useAppStore((s) => s.viewAs);

  const syncDeckMutation = useMutation({
    mutationFn: (force: boolean) => syncPitchDeck(lead.id, force),
    // A deck attaching queues re-assessment on the backend; the lead leaves
    // `awaiting_deck` once that completes. Invalidate now (covers the
    // "already had a deck, still garbled" case) and again after the async
    // assessment has had time to run, mirroring LeadsPage's sync polling.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      setTimeout(() => qc.invalidateQueries({ queryKey: ["leads"] }), 12000);
    },
  });

  const reassessMutation = useMutation({
    mutationFn: () => reassess(lead.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const busy = syncDeckMutation.isPending || reassessMutation.isPending;

  return (
    <div
      className="bg-card border border-border rounded-2xl p-5 space-y-3 shadow-sm"
      data-testid="awaiting-deck-card"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          {onToggleSelect && (
            <SelectCheckbox
              checked={!!selected}
              onChange={onToggleSelect}
              readOnly={readOnly}
              label={`Select ${lead.company_name} for bulk archive`}
            />
          )}
          <div className="min-w-0">
          <p className="font-semibold text-foreground text-sm">{lead.company_name}</p>
          <PriorContactChip
            priorContact={lead.prior_contact}
            priorContactCount={lead.prior_contact_count}
            priorContactLastAt={lead.prior_contact_last_at}
          />
          <p className="text-xs text-muted-foreground mt-0.5">
            {lead.stage ?? "—"} · {lead.region ?? "—"}
          </p>
          {(lead.company_linkedin_url || lead.website) && (
            <div className="flex items-center gap-2 mt-1 text-xs">
              {lead.company_linkedin_url && (
                <a
                  href={lead.company_linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-info hover:underline"
                >
                  LinkedIn ↗
                </a>
              )}
              {lead.website && (
                <a
                  href={lead.website.startsWith("http") ? lead.website : `https://${lead.website}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  Website ↗
                </a>
              )}
            </div>
          )}
          </div>
        </div>
        <Badge label="Awaiting Deck" variant="maybe" />
      </div>

      <div className="rounded-xl bg-muted/40 border border-border p-3 text-xs text-muted-foreground">
        No pitch deck yet — parked here instead of being scored on thin context.
        {lead.pitch_deck_filename && (
          <span className="block mt-1 text-foreground" title={lead.pitch_deck_filename}>
            On file: {lead.pitch_deck_filename} (attached but no readable text was
            extracted — re-fetch to retry)
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap pt-1">
        <button
          onClick={() => syncDeckMutation.mutate(!!lead.pitch_deck_drive_id)}
          disabled={busy || readOnly}
          title={readOnly ? "Read-only while viewing another user's board" : undefined}
          data-testid="fetch-pitch-deck-btn"
          className="px-3 py-1.5 text-xs rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {syncDeckMutation.isPending
            ? "Fetching…"
            : lead.pitch_deck_drive_id
              ? "Re-fetch"
              : "Fetch pitch deck"}
        </button>
        <button
          onClick={() => reassessMutation.mutate()}
          disabled={busy || readOnly}
          title={readOnly ? "Read-only while viewing another user's board" : undefined}
          className="px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {reassessMutation.isPending ? "Reassessing…" : "Reassess"}
        </button>
      </div>

      {syncDeckMutation.data && (
        <p
          className={`text-[10px] leading-snug ${
            syncDeckMutation.data.attached ? "text-muted-foreground" : "text-error"
          }`}
          data-testid="fetch-pitch-deck-reason"
        >
          {syncDeckMutation.data.reason}
        </p>
      )}
      {syncDeckMutation.isError && (
        <p className="text-[10px] text-error" data-testid="fetch-pitch-deck-reason">
          Couldn't reach the fetch endpoint — please try again.
        </p>
      )}
    </div>
  );
}

export default function AwaitingDeckPage() {
  const [sort, setSort] = useState<SortOrder>("newest");
  const readOnly = !!useAppStore((s) => s.viewAs);
  const qc = useQueryClient();
  const toast = useToast();

  // Sharing AWAITING_DECK_QUERY_KEY as-is (rather than appending sort) keeps
  // this on the same cache entry as Navbar's count badge in the common case
  // (default "newest"); it only splits into a second cache entry while the
  // user has explicitly picked "oldest" here.
  const { data, isLoading } = useQuery({
    queryKey: sort === "newest" ? AWAITING_DECK_QUERY_KEY : [...AWAITING_DECK_QUERY_KEY, sort],
    queryFn: () => fetchLeads({ status: "awaiting_deck", page_size: 1000, sort }),
    refetchInterval: 15_000,
  });

  const leads = data?.items ?? [];
  const selectableIds = useMemo(() => leads.map((l) => l.id), [leads]);
  const { selected, toggle, selectAll, clear, allSelected } = useBulkSelection(selectableIds);

  const bulkArchive = useMutation({
    mutationFn: () => bulkArchiveLeads(Array.from(selected)),
    onSuccess: (result) => {
      clear();
      const parts = [`Archived ${result.archived} · synced to Copper`];
      if (result.failed.length > 0) parts.push(`${result.failed.length} failed`);
      toast(parts.join(" · "));
    },
    onError: () => toast("Couldn't archive the selected leads — please try again."),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
    },
  });

  const handleBulkArchive = () => {
    const n = selected.size;
    if (confirm(`Archive ${n} lead${n === 1 ? "" : "s"}? Each one is also marked Unqualified in Copper.`)) {
      bulkArchive.mutate();
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Awaiting Pitch Deck</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Leads parked here have no usable pitch deck yet, so they haven't been scored.
            They rejoin the Deal Flow board automatically once a deck attaches and
            re-assessment completes.
          </p>
        </div>
        <SortToggle value={sort} onChange={setSort} />
      </div>

      {!isLoading && leads.length > 0 && (
        <BulkArchiveBar
          count={selected.size}
          total={selectableIds.length}
          allSelected={allSelected}
          onSelectAll={selectAll}
          onClear={clear}
          onArchiveSelected={handleBulkArchive}
          archiving={bulkArchive.isPending}
          readOnly={readOnly}
        />
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      ) : leads.length === 0 ? (
        <div className="border border-dashed border-border rounded-2xl bg-card/50 py-16 px-6 text-center max-w-md mx-auto">
          <h3 className="font-heading text-base font-semibold text-foreground">
            Nobody's waiting on a deck
          </h3>
          <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
            Deckless leads will show up here instead of being auto-rejected.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {leads.map((lead) => (
            <AwaitingDeckCard
              key={lead.id}
              lead={lead}
              selected={selected.has(lead.id)}
              onToggleSelect={() => toggle(lead.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
