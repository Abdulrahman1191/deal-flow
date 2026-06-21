import type { Lead } from "../../types/lead";

interface Props {
  leads: Lead[];
}

export default function StatsRow({ leads: all }: Props) {
  const bucket = (b: string) =>
    all.filter((l) => (l.assessment?.user_override ?? l.assessment?.bucket) === b).length;

  const stats = [
    { label: "Total Inbound", value: all.length, color: "text-foreground", dot: "bg-primary" },
    { label: "Yes — Meet", value: bucket("YES"), color: "text-success", dot: "bg-success" },
    { label: "Maybe — Review", value: bucket("MAYBE"), color: "text-warning", dot: "bg-warning" },
    { label: "Auto-Reject", value: bucket("REJECT"), color: "text-error", dot: "bg-error" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      {stats.map((s) => (
        <div
          key={s.label}
          className="bg-card border border-border rounded-2xl p-4 sm:p-5 shadow-sm hover:shadow-md transition-shadow"
        >
          <div className="flex items-center gap-2 mb-2">
            <span className={`h-2 w-2 rounded-full ${s.dot}`} />
            <p className="text-xs font-medium text-muted-foreground">{s.label}</p>
          </div>
          <p className={`text-3xl font-semibold tracking-tight ${s.color}`}>{s.value}</p>
        </div>
      ))}
    </div>
  );
}
