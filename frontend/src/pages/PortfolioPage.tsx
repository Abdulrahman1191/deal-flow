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
  FUNDED: "text-green-300 bg-green-500/10",
  PASSED: "text-red-300 bg-red-500/10",
  NOT_SEEN: "text-gray-300 bg-gray-700/30",
};

const STATUS_COLOR: Record<OutcomeStatus, string> = {
  exited: "text-emerald-300 bg-emerald-500/10",
  growing: "text-green-300 bg-green-500/10",
  stalled: "text-yellow-300 bg-yellow-500/10",
  zombie: "text-orange-300 bg-orange-500/10",
  failed: "text-red-300 bg-red-500/10",
  acqui_hire: "text-purple-300 bg-purple-500/10",
  too_early: "text-blue-300 bg-blue-500/10",
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
      return <p className="p-6 text-sm text-gray-500">Portfolio view is owner-only.</p>;
    }
    return <p className="p-6 text-sm text-red-400">Failed to load portfolio.</p>;
  }

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header + stats */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Portfolio Intelligence</h1>
          <p className="text-sm text-gray-500 mt-1">
            Companies we've funded, passed on, or learned from. Outcomes + signals feed the AI.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors"
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
        <p className="text-sm text-gray-500">Loading…</p>
      ) : companies.length === 0 ? (
        <div className="border border-dashed border-gray-800 rounded-xl p-12 text-center">
          <p className="text-gray-400">No companies yet.</p>
          <p className="text-xs text-gray-600 mt-2">
            Add your first one with the button above. Start with 5 you remember well.
          </p>
        </div>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-950 text-xs uppercase tracking-wider text-gray-500">
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
    green: "text-green-300",
    red: "text-red-300",
    gray: "text-gray-300",
    emerald: "text-emerald-300",
  }[tone ?? "gray"];
  return (
    <button
      onClick={onClick}
      className={`bg-gray-900 border ${
        active ? "border-blue-500" : "border-gray-800"
      } rounded-xl px-4 py-3 text-left hover:border-gray-700 transition-colors`}
      disabled={!onClick}
    >
      <p className="text-[10px] uppercase tracking-wider text-gray-500">{label}</p>
      <p className={`text-xl font-semibold mt-1 ${tone ? toneClass : "text-white"}`}>
        {value}
      </p>
    </button>
  );
}

function CompanyRow({ c, onClick }: { c: PortfolioCompany; onClick: () => void }) {
  return (
    <tr
      onClick={onClick}
      className="border-t border-gray-800 hover:bg-gray-850 cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-white">{c.name}</p>
          {c.region && <p className="text-[11px] text-gray-500 mt-0.5">{c.region}</p>}
        </div>
      </td>
      <td className="px-4 py-3 text-gray-300">{c.sector ?? "—"}</td>
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
      <td className="px-4 py-3 text-gray-400">{c.signal_count}</td>
      <td className="px-4 py-3 text-gray-500 text-xs">
        {c.last_reviewed_at
          ? new Date(c.last_reviewed_at).toLocaleDateString("en-GB")
          : "—"}
      </td>
    </tr>
  );
}
