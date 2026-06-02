import type { Lead } from "../../types/lead";

interface Props {
  leads: Lead[];
}

export default function StatsRow({ leads: all }: Props) {
  const bucket = (b: string) =>
    all.filter((l) => (l.assessment?.user_override ?? l.assessment?.bucket) === b).length;

  const stats = [
    { label: "Total Inbound", value: all.length, color: "text-purple-400" },
    { label: "Yes — Meet", value: bucket("YES"), color: "text-green-400" },
    { label: "Maybe — Review", value: bucket("MAYBE"), color: "text-yellow-400" },
    { label: "Auto-Reject", value: bucket("REJECT"), color: "text-red-400" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {stats.map((s) => (
        <div key={s.label} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <p className="text-xs text-gray-500 mb-1">{s.label}</p>
          <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
        </div>
      ))}
    </div>
  );
}
