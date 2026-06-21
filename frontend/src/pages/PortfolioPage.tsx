import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCompanies,
  fetchStats,
  fetchVocab,
} from "../api/portfolio";
import type {
  Decision,
  OutcomeStatus,
  PortfolioCompany,
} from "../types/portfolio";
import PortfolioCompanyDetailModal from "../components/portfolio/PortfolioCompanyDetailModal";
import AddCompanyModal from "../components/portfolio/AddCompanyModal";

const DECISION_COLOR: Record<Decision, string> = {
  FUNDED: "text-success bg-success/10",
  PASSED: "text-error bg-error/10",
  NOT_SEEN: "text-foreground bg-muted/30",
};

const STATUS_COLOR: Record<OutcomeStatus, string> = {
  exited: "text-success bg-success/10",
  growing: "text-success bg-success/10",
  stalled: "text-warning bg-warning/10",
  zombie: "text-warning bg-warning/10",
  failed: "text-error bg-error/10",
  acqui_hire: "text-primary bg-primary/10",
  too_early: "text-info bg-primary/10",
};

export default function PortfolioPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [decisionFilter, setDecisionFilter] = useState<Decision | null>(null);

  const { data: companies = [], isLoading, isError, error } = useQuery({
    queryKey: ["portfolio-companies", decisionFilter],
    queryFn: () =>
      fetchCompanies(decisionFilter ? { decision: decisionFilter } : undefined),
  });

  const { data: stats } = useQuery({
    queryKey: ["portfolio-stats"],
    queryFn: fetchStats,
  });

  const { data: vocab } = useQuery({
    queryKey: ["portfolio-vocab"],
    queryFn: fetchVocab,
  });

  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Portfolio view is owner-only.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load portfolio.</p>;
  }

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto space-y-6">
      {/* Header + stats */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Portfolio Intelligence</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Companies we've funded, passed on, or learned from. Outcomes + signals feed the AI.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="px-4 py-2 text-sm rounded-lg bg-primary hover:bg-primary/90 text-white transition-colors"
        >
          + Add company
        </button>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard
            label="Total"
            value={stats.totals.total}
            active={decisionFilter === null}
            onClick={() => setDecisionFilter(null)}
          />
          <StatCard
            label="Funded"
            value={stats.totals.by_decision.funded}
            tone="green"
            active={decisionFilter === "FUNDED"}
            onClick={() => setDecisionFilter("FUNDED")}
          />
          <StatCard
            label="Passed"
            value={stats.totals.by_decision.passed}
            tone="red"
            active={decisionFilter === "PASSED"}
            onClick={() => setDecisionFilter("PASSED")}
          />
          <StatCard
            label="Not seen (retro)"
            value={stats.totals.by_decision.not_seen}
            tone="gray"
            active={decisionFilter === "NOT_SEEN"}
            onClick={() => setDecisionFilter("NOT_SEEN")}
          />
          <StatCard
            label="Exited"
            value={stats.totals.by_status.exited}
            tone="emerald"
            active={false}
          />
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : companies.length === 0 ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center">
          <p className="text-muted-foreground">No companies yet.</p>
          <p className="text-xs text-muted-foreground mt-2">
            Add your first one with the button above. Start with 5 you remember well.
          </p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-x-auto">
          <table className="w-full min-w-[680px] text-sm">
            <thead className="bg-background text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-3 font-medium">Company</th>
                <th className="text-left px-4 py-3 font-medium">Sector</th>
                <th className="text-left px-4 py-3 font-medium">Decision</th>
                <th className="text-left px-4 py-3 font-medium">Current status</th>
                <th className="text-left px-4 py-3 font-medium">Signals</th>
                <th className="text-left px-4 py-3 font-medium">Reviewed</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <CompanyRow key={c.id} c={c} onClick={() => setSelectedId(c.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedId && vocab && (
        <PortfolioCompanyDetailModal
          companyId={selectedId}
          vocab={vocab}
          onClose={() => setSelectedId(null)}
        />
      )}

      {showAdd && vocab && (
        <AddCompanyModal vocab={vocab} onClose={() => setShowAdd(false)} />
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  tone?: "green" | "red" | "gray" | "emerald";
  active: boolean;
  onClick?: () => void;
}) {
  const toneClass = {
    green: "text-success",
    red: "text-error",
    gray: "text-foreground",
    emerald: "text-success",
  }[tone ?? "gray"];
  return (
    <button
      onClick={onClick}
      className={`bg-card border ${
        active ? "border-primary" : "border-border"
      } rounded-xl px-4 py-3 text-left hover:border-border transition-colors`}
      disabled={!onClick}
    >
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`text-xl font-semibold mt-1 ${tone ? toneClass : "text-foreground"}`}>
        {value}
      </p>
    </button>
  );
}

function CompanyRow({ c, onClick }: { c: PortfolioCompany; onClick: () => void }) {
  return (
    <tr
      onClick={onClick}
      className="border-t border-border hover:bg-muted cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-foreground">{c.name}</p>
          {c.region && <p className="text-[11px] text-muted-foreground mt-0.5">{c.region}</p>}
        </div>
      </td>
      <td className="px-4 py-3 text-foreground">{c.sector ?? "—"}</td>
      <td className="px-4 py-3">
        <span
          className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${DECISION_COLOR[c.our_decision]}`}
        >
          {c.our_decision.replace("_", " ")}
        </span>
      </td>
      <td className="px-4 py-3">
        <span
          className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${STATUS_COLOR[c.current_status]}`}
        >
          {c.current_status.replace("_", " ")}
        </span>
      </td>
      <td className="px-4 py-3 text-muted-foreground">{c.signal_count}</td>
      <td className="px-4 py-3 text-muted-foreground text-xs">
        {c.last_reviewed_at
          ? new Date(c.last_reviewed_at).toLocaleDateString("en-GB")
          : "—"}
      </td>
    </tr>
  );
}
