import type { Lead } from "../../types/lead";

interface Props {
  leads: Lead[];
}

export default function StatsRow({ leads: all }: Props) {
  const bucket = (b: string) =>
    all.filter((l) => (l.assessment?.user_override ?? l.assessment?.bucket) === b).length;

  const stats = [
    { label: "Total Inbound", value: all.length, color: "text-primary" },
    { label: "Yes — Meet", value: bucket("YES"), color: "text-success" },
    { label: "Maybe — Review", value: bucket("MAYBE"), color: "text-warning" },
    { label: "Auto-Reject", value: bucket("REJECT"), color: "text-error" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {stats.map((s) => (
        <div key={s.label} className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted-foreground mb-1">{s.label}</p>
          <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
        </div>
      ))}
    </div>
  );
}
