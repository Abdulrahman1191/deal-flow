import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { fetchLeads, syncMyLeads } from "../api/leads";
import useAppStore from "../store/useAppStore";
import StatsRow from "../components/leads/StatsRow";
import LeadBucket from "../components/leads/LeadBucket";
import LeadCard from "../components/leads/LeadCard";
import type { Lead } from "../types/lead";

function LeadCardPending({ lead }: { lead: Lead }) {
  return <LeadCard lead={lead} />;
}

export default function LeadsPage() {
  const { leads, setLeads } = useAppStore();
  const qc = useQueryClient();
  const didAutoSync = useRef(false);

  const { data, isLoading } = useQuery({
    queryKey: ["leads"],
    queryFn: () => fetchLeads({ page_size: 1000 }),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (data?.items) setLeads(data.items);
  }, [data, setLeads]);

  // Sync queues a Celery import; results land via the 15s refetch. We also nudge
  // a refetch shortly after so freshly-imported leads show without the full wait.
  const sync = useMutation({
    mutationFn: syncMyLeads,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["leads"] }), 4000);
      setTimeout(() => qc.invalidateQueries({ queryKey: ["leads"] }), 12000);
    },
  });

  // Pull this user's Copper leads once on first mount.
  useEffect(() => {
    if (didAutoSync.current) return;
    didAutoSync.current = true;
    sync.mutate();
  }, [sync]);

  const bucket = (b: string) =>
    leads.filter((l: Lead) => (l.assessment?.user_override ?? l.assessment?.bucket) === b);

  if (isLoading) {
    return <p className="text-muted-foreground text-sm p-6">Loading leads…</p>;
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-foreground">Deal Flow</h1>
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
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
          {sync.isPending ? "Syncing…" : "Sync my leads"}
        </button>
      </div>
      <StatsRow leads={leads} />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <LeadBucket title="Yes — Schedule Meeting" leads={bucket("YES")} accent="bg-success" />
        <LeadBucket title="Maybe — Review" leads={bucket("MAYBE")} accent="bg-warning" />
        <LeadBucket title="Reject" leads={bucket("REJECT")} accent="bg-error" />
      </div>
      {leads.filter((l) => !l.assessment).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground mb-3">Pending Assessment</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {leads.filter((l) => !l.assessment).map((lead) => (
              <LeadCardPending key={lead.id} lead={lead} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
