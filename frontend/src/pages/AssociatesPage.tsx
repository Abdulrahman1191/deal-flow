import { useQuery } from "@tanstack/react-query";
import { fetchAssociatesPerformance } from "../api/associates";
import type { AssociatePerformance } from "../api/associates";

const COLUMNS: { key: keyof AssociatePerformance; label: string }[] = [
  { key: "leads_total", label: "Leads" },
  { key: "backlog", label: "Backlog" },
  { key: "awaiting_deck", label: "Awaiting deck" },
  { key: "active", label: "Active" },
  { key: "outreach_sent", label: "Outreach sent" },
  { key: "approved", label: "Approved" },
  { key: "converted", label: "Converted" },
  { key: "archived", label: "Archived" },
];

function sumColumn(rows: AssociatePerformance[], key: keyof AssociatePerformance): number {
  return rows.reduce((sum, r) => sum + (r[key] as number), 0);
}

export default function AssociatesPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["associates-performance"],
    queryFn: fetchAssociatesPerformance,
  });

  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Associates view is admin-only.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load associate performance.</p>;
  }

  const associates = data?.associates ?? [];

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Associates</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Lead-management throughput per client-facing associate, at a glance.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : associates.length === 0 ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center">
          <p className="text-muted-foreground">No associate data yet.</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-x-auto">
          <table className="w-full min-w-[780px] text-sm">
            <thead className="bg-background text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="text-left px-4 py-3 font-medium">Associate</th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className="text-right px-4 py-3 font-medium">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {associates.map((a) => (
                <tr key={a.email} className="border-t border-border">
                  <td className="px-4 py-3 text-foreground">{a.email}</td>
                  {COLUMNS.map((c) => (
                    <td key={c.key} className="px-4 py-3 text-right text-muted-foreground">
                      {a[c.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border bg-background font-medium">
                <td className="px-4 py-3 text-foreground">Total</td>
                {COLUMNS.map((c) => (
                  <td key={c.key} className="px-4 py-3 text-right text-foreground">
                    {sumColumn(associates, c.key)}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
