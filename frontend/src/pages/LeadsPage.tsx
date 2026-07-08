import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { exportLeadsCsv, fetchLeads } from "../api/leads";
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
  const [exporting, setExporting] = useState(false);

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

  const handleExportYes = async () => {
    setExporting(true);
    try {
      const blob = await exportLeadsCsv("YES");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "yes_leads.csv";
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  if (isLoading) {
    return <p className="text-gray-500 text-sm p-6">Loading leads…</p>;
  }

  return (
    <div className="p-6 space-y-6">
      <StatsRow leads={leads} />
      <div className="flex justify-end">
        <button
          onClick={handleExportYes}
          disabled={exporting}
          className="text-xs px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-800 text-gray-300 hover:bg-gray-850 disabled:opacity-50 transition-colors"
        >
          {exporting ? "Exporting…" : "Export CSV"}
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <LeadBucket title="Yes — Schedule Meeting" leads={bucket("YES")} accent="bg-green-500" />
        <LeadBucket title="Maybe — Review" leads={bucket("MAYBE")} accent="bg-yellow-500" />
        <LeadBucket title="Reject" leads={bucket("REJECT")} accent="bg-red-500" />
      </div>
      {leads.filter((l) => !l.assessment).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-500 mb-3">Pending Assessment</h3>
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
