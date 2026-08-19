import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchCalibrationStats, fetchOverrides } from "../api/overrides";

const BUCKET_COLOR: Record<string, string> = {
  YES: "text-success bg-success/10",
  MAYBE: "text-warning bg-warning/10",
  REJECT: "text-error bg-error/10",
};

const ARTICULATION_WARNING_THRESHOLD = 0.5;

function pct(rate: number | null | undefined) {
  return rate == null ? "—" : `${Math.round(rate * 100)}%`;
}

function BucketBadge({ bucket }: { bucket: string }) {
  return (
    <span
      className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${BUCKET_COLOR[bucket] ?? "text-foreground bg-muted/30"}`}
    >
      {bucket}
    </span>
  );
}

function EmptySection({ children }: { children: string }) {
  return (
    <div className="border border-dashed border-border rounded-xl p-8 text-center">
      <p className="text-sm text-muted-foreground">{children}</p>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "primary" }) {
  return (
    <div className="bg-card border border-border rounded-2xl p-4 sm:p-5">
      <p className="text-xs font-medium text-muted-foreground mb-2">{label}</p>
      <p className={`text-3xl font-semibold tracking-tight ${tone === "primary" ? "text-primary" : "text-foreground"}`}>
        {value}
      </p>
    </div>
  );
}

function WeekTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 text-xs shadow-md">
      <p className="text-foreground font-medium">{row.label}</p>
      <p className="text-muted-foreground mt-1">
        {row.agreements} / {row.total} agreed ({pct(row.agreement_rate)})
      </p>
    </div>
  );
}

export default function CalibrationPage() {
  const { data: stats, isLoading, isError, error } = useQuery({
    queryKey: ["calibration-stats"],
    queryFn: fetchCalibrationStats,
  });

  const { data: disagreements = [] } = useQuery({
    queryKey: ["overrides", "disagreements"],
    queryFn: () => fetchOverrides({ only_disagreements: true, limit: 10 }),
    enabled: !!stats,
  });

  if (isLoading) return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Loading calibration data…</p>;
  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Calibration is only visible to the owner.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load calibration data.</p>;
  }
  if (!stats) return null;

  const weeklyData = stats.agreement_over_time.map((w) => ({
    ...w,
    label: new Date(w.week_start).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }),
    ratePct: Math.round(w.agreement_rate * 100),
  }));

  const pairEntries = Object.entries(stats.disagreement_pairs).sort((a, b) => b[1] - a[1]);
  const maxPairCount = pairEntries.length ? pairEntries[0][1] : 0;

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Calibration</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Is the AI getting better, and which partners rate carefully? Excludes QA/test
          accounts ({stats.excluded_test_accounts.join(", ")}).
        </p>
      </div>

      {stats.total_rows === 0 ? (
        <EmptySection>No rated assessments yet — calibration stats will appear once the team starts confirming or overriding AI ratings.</EmptySection>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
            <StatCard label="Agreement rate" value={pct(stats.agreement_rate)} tone="primary" />
            <StatCard label="Total corrections" value={String(stats.total_rows)} />
            <StatCard label="Agreements" value={String(stats.agreements)} />
            <StatCard label="Disagreements" value={String(stats.disagreements)} />
          </div>

          {/* Weekly trend */}
          <section className="bg-card border border-border rounded-xl p-4 sm:p-5 space-y-3">
            <h2 className="text-sm font-semibold text-foreground">Agreement rate — weekly</h2>
            {weeklyData.length === 0 ? (
              <EmptySection>Not enough history yet for a trend.</EmptySection>
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={weeklyData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                    <XAxis
                      dataKey="label"
                      tick={{ fontSize: 11, fill: "#64748B" }}
                      axisLine={{ stroke: "#E2E8F0" }}
                      tickLine={false}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tickFormatter={(v) => `${v}%`}
                      tick={{ fontSize: 11, fill: "#64748B" }}
                      axisLine={false}
                      tickLine={false}
                      width={40}
                    />
                    <Tooltip content={<WeekTooltip />} cursor={{ fill: "#E2E8F0", opacity: 0.4 }} />
                    <Bar dataKey="ratePct" fill="#1F2533" radius={[4, 4, 0, 0]} maxBarSize={28} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </section>

          {/* Per-partner table */}
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Per-partner profile</h2>
            {stats.partner_profiles.length === 0 ? (
              <EmptySection>No partner activity yet.</EmptySection>
            ) : (
              <div className="bg-card border border-border rounded-xl overflow-x-auto">
                <table className="w-full min-w-[680px] text-sm">
                  <thead className="bg-background text-xs uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium">Partner</th>
                      <th className="text-right px-4 py-3 font-medium">Ratings</th>
                      <th className="text-right px-4 py-3 font-medium">Agreement</th>
                      <th className="text-right px-4 py-3 font-medium">Confirm rate</th>
                      <th className="text-right px-4 py-3 font-medium">Correction rate</th>
                      <th className="text-right px-4 py-3 font-medium">Articulation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.partner_profiles.map((p) => {
                      const lowArticulation = p.articulation_rate < ARTICULATION_WARNING_THRESHOLD;
                      return (
                        <tr key={p.acted_by_email} className="border-t border-border">
                          <td className="px-4 py-3 text-foreground">{p.acted_by_email}</td>
                          <td className="px-4 py-3 text-right text-muted-foreground">{p.total}</td>
                          <td className="px-4 py-3 text-right text-foreground">{pct(p.agreement_rate)}</td>
                          <td className="px-4 py-3 text-right text-muted-foreground">{pct(p.confirm_rate)}</td>
                          <td className="px-4 py-3 text-right text-muted-foreground">{pct(p.correction_rate)}</td>
                          <td className="px-4 py-3 text-right">
                            <span className={lowArticulation ? "text-warning font-medium" : "text-muted-foreground"}>
                              {pct(p.articulation_rate)}
                            </span>
                            {lowArticulation && (
                              <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded text-warning bg-warning/10">
                                Low articulation
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Disagreement breakdown */}
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Disagreement breakdown</h2>
            {pairEntries.length === 0 ? (
              <EmptySection>No disagreements recorded yet.</EmptySection>
            ) : (
              <div className="bg-card border border-border rounded-xl p-4 space-y-2">
                {pairEntries.map(([pair, count]) => (
                  <div key={pair} className="flex items-center gap-3 text-sm">
                    <span className="w-32 shrink-0 text-foreground">{pair.replace("→", " → ")}</span>
                    <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${maxPairCount ? (count / maxPairCount) * 100 : 0}%` }}
                      />
                    </div>
                    <span className="w-8 text-right text-muted-foreground">{count}</span>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mt-4">
              Recent disagreements
            </h3>
            {disagreements.length === 0 ? (
              <EmptySection>No individual disagreements yet.</EmptySection>
            ) : (
              <div className="space-y-2">
                {disagreements.map((o) => (
                  <div key={o.id} className="bg-card border border-border rounded-xl p-3 space-y-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <BucketBadge bucket={o.ai_bucket} />
                      <span className="text-muted-foreground text-xs">→</span>
                      <BucketBadge bucket={o.human_bucket} />
                      <span className="text-xs text-muted-foreground ml-auto">
                        {new Date(o.created_at).toLocaleString("en-GB")}
                      </span>
                    </div>
                    {o.ai_summary && (
                      <p className="text-sm text-foreground leading-relaxed">{o.ai_summary}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
