const criteria = [
  { name: "MENA Focus", weight: "20%", desc: "Operating in or targeting the MENA region." },
  { name: "Deep Technology", weight: "25%", desc: "Proprietary, defensible tech at the core of the business." },
  { name: "Strong IP", weight: "20%", desc: "Patents, proprietary data, or hardware moats." },
  { name: "Experienced Team", weight: "20%", desc: "Domain expertise and relevant prior background." },
  { name: "Stage Alignment", weight: "10%", desc: "Pre-seed to Series A only." },
  { name: "Model Fit", weight: "5%", desc: "Not a marketplace or basic SaaS without deep tech." },
];

const hardRejects = [
  "Traditional marketplace model without a deep tech layer",
  "Basic SaaS with no IP or tech differentiation",
  "No MENA presence, operations, or target market",
  "Series B or later stage",
];

const thresholds = [
  { range: "80–100", label: "YES", desc: "Schedule a meeting", color: "text-green-400" },
  { range: "50–79", label: "MAYBE", desc: "Flag for review", color: "text-yellow-400" },
  { range: "0–49", label: "REJECT", desc: "Draft rejection email", color: "text-red-400" },
];

export default function FrameworkPage() {
  return (
    <div className="p-6 max-w-3xl space-y-8">
      <div>
        <h1 className="text-lg font-semibold text-white mb-1">Investment Framework</h1>
        <p className="text-xs text-gray-500">Raed Ventures · Sector-agnostic early-stage deep tech, MENA focus</p>
      </div>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Scoring Criteria</h2>
        <div className="space-y-2">
          {criteria.map((c) => (
            <div key={c.name} className="flex gap-4 bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="w-16 shrink-0">
                <span className="text-lg font-bold text-brand">{c.weight}</span>
              </div>
              <div>
                <p className="text-sm font-medium text-white">{c.name}</p>
                <p className="text-xs text-gray-500 mt-0.5">{c.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Score Thresholds</h2>
        <div className="flex gap-4">
          {thresholds.map((t) => (
            <div key={t.label} className="flex-1 bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
              <p className={`text-xl font-bold ${t.color}`}>{t.range}</p>
              <p className={`text-sm font-semibold mt-1 ${t.color}`}>{t.label}</p>
              <p className="text-xs text-gray-500 mt-1">{t.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Hard Reject Criteria</h2>
        <div className="bg-gray-900 border border-red-900/40 rounded-xl p-4 space-y-2">
          {hardRejects.map((r) => (
            <div key={r} className="flex gap-2 text-xs text-red-400">
              <span className="shrink-0 mt-0.5">✗</span>
              <span>{r}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
