import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { fetchLeads } from "../api/leads";
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

  const { data, isLoading } = useQuery({
    queryKey: ["leads"],
    queryFn: () => fetchLeads({ page_size: 1000 }),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (data?.items) setLeads(data.items);
  }, [data, setLeads]);

  const bucket = (b: string) =>
    leads.filter((l: Lead) => (l.assessment?.user_override ?? l.assessment?.bucket) === b);

  if (isLoading) {
    return <p className="text-muted-foreground text-sm p-6">Loading leads…</p>;
  }

  return (
    <div className="p-6 space-y-6">
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
