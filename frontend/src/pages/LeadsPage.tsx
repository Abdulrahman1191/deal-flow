import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchLeads, syncMyLeads } from "../api/leads";
import useAppStore from "../store/useAppStore";
import StatsRow from "../components/leads/StatsRow";
import LeadBucket from "../components/leads/LeadBucket";
import LeadCard from "../components/leads/LeadCard";
import type { Lead } from "../types/lead";

function LeadCardPending({ lead }: { lead: Lead }) {
  return <LeadCard lead={lead} />;
}

function SkeletonCard() {
  return (
    <div className="bg-card border border-border rounded-2xl p-5 shadow-sm space-y-3 animate-pulse">
      <div className="flex justify-between">
        <div className="space-y-2">
          <div className="h-3.5 w-32 rounded bg-muted" />
          <div className="h-2.5 w-20 rounded bg-muted" />
        </div>
        <div className="h-5 w-12 rounded-full bg-muted" />
      </div>
      <div className="h-16 rounded-xl bg-muted/60" />
      <div className="h-2 w-full rounded bg-muted" />
      <div className="h-8 w-40 rounded-lg bg-muted" />
    </div>
  );
}

export default function LeadsPage() {
  const { leads, setLeads } = useAppStore();
  const qc = useQueryClient();
  const didAutoSync = useRef(false);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["leads"],
    queryFn: () => fetchLeads({ page_size: 1000 }),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (data?.items) setLeads(data.items);
  }, [data, setLeads]);

  const sync = useMutation({
    mutationFn: syncMyLeads,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["leads"] }), 4000);
      setTimeout(() => qc.invalidateQueries({ queryKey: ["leads"] }), 12000);
    },
  });

  useEffect(() => {
    if (didAutoSync.current) return;
    didAutoSync.current = true;
    sync.mutate();
  }, [sync]);

  // Distinct stages for the filter dropdown.
  const stages = useMemo(
    () => Array.from(new Set(leads.map((l) => l.stage).filter(Boolean))) as string[],
    [leads],
  );

  // Client-side search + stage filter (board already loads the full pipeline).
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return leads.filter((l) => {
      if (q && !l.company_name.toLowerCase().includes(q)) return false;
      if (stage && l.stage !== stage) return false;
      return true;
    });
  }, [leads, search, stage]);

  const bucket = (b: string) =>
    filtered.filter((l: Lead) => (l.assessment?.user_override ?? l.assessment?.bucket) === b);
  const hasFilter = !!search.trim() || !!stage;

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-foreground mr-auto">Deal Flow</h1>
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" width="14" height="14"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          >
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search company…"
            className="w-44 sm:w-56 bg-card border border-border rounded-lg pl-8 pr-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-ring transition-colors"
          />
        </div>
        {stages.length > 0 && (
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring transition-colors"
          >
            <option value="">All stages</option>
            {stages.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )}
        <button
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
          className="shrink-0 px-3.5 py-2 text-xs font-medium rounded-lg bg-card border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50 inline-flex items-center gap-2"
          title="Pull your latest Copper-assigned leads"
        >
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className={sync.isPending ? "animate-spin" : ""}
          >
            <path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" />
          </svg>
          {sync.isPending ? "Syncing…" : "Sync"}
        </button>
      </div>

      {isLoading ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-24 rounded-2xl bg-card border border-border animate-pulse" />
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {Array.from({ length: 3 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        </>
      ) : leads.length === 0 ? (
        <div className="border border-dashed border-border rounded-2xl bg-card/50 py-16 px-6 text-center max-w-md mx-auto">
          <h3 className="font-heading text-base font-semibold text-foreground">No deals yet</h3>
          <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
            Your Copper-assigned deals will appear here once they're imported.
            {sync.isPending ? " Syncing now…" : " Hit Sync to pull them in."}
          </p>
          <button
            onClick={() => sync.mutate()}
            disabled={sync.isPending}
            className="mt-4 px-4 py-2 text-sm font-medium rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors disabled:opacity-50"
          >
            {sync.isPending ? "Syncing…" : "Sync my leads"}
          </button>
        </div>
      ) : (
        <>
          <StatsRow leads={filtered} />
          {hasFilter && bucket("YES").length + bucket("MAYBE").length + bucket("REJECT").length === 0 ? (
            <div className="border border-dashed border-border rounded-2xl py-12 text-center text-sm text-muted-foreground">
              No deals match your search.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <LeadBucket title="Yes — Schedule Meeting" leads={bucket("YES")} accent="bg-success" />
              <LeadBucket title="Maybe — Review" leads={bucket("MAYBE")} accent="bg-warning" />
              <LeadBucket title="Reject" leads={bucket("REJECT")} accent="bg-error" />
            </div>
          )}
          {filtered.filter((l) => !l.assessment).length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-muted-foreground mb-3">Pending Assessment</h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {filtered.filter((l) => !l.assessment).map((lead) => (
                  <LeadCardPending key={lead.id} lead={lead} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
