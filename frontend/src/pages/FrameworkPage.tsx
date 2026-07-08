// The assessment is pattern-based, not a weighted score. The AI reasons by
// analogy to Raed's labeled portfolio retrospective (42 past bets, each with a
// thesis, kill criterion, outcome, and hindsight verdict) and decides where a
// new lead fits. This page documents that framework for the team.

const buckets = [
  {
    label: "YES",
    head: "Worth a meeting",
    color: "text-success",
    border: "border-success/40",
    desc: "Resembles the patterns of bets that worked for the reasons we underwrote. Credible signal on the thesis and Raed's filters — enough to want a conversation, even if not every detail is confirmed yet.",
  },
  {
    label: "MAYBE",
    head: "Flag for review",
    color: "text-warning",
    border: "border-warning/40",
    desc: "Genuinely mixed, or interesting but unresolved from the evidence available. Routed to you to make the call — this is where thin-but-promising leads land.",
  },
  {
    label: "REJECT",
    head: "Clear poor fit",
    color: "text-error",
    border: "border-error/40",
    desc: "Affirmative poor fit on the evidence we DO have — repeats a known failure pattern, or plainly fails a Raed filter. Not used for 'we couldn't find enough data' — that's a MAYBE.",
  },
];

const filters = [
  { name: "Founder Obsession", desc: "Concrete evidence of grit, recruiting power, and customer obsession — not just background credentials." },
  { name: "Market Scale", desc: "GCC TAM consistent with a meaningful exit on the Raed check size. If the market isn't there, flag it." },
  { name: "Unfair Advantage", desc: "The one thing that compounds. If it takes a paragraph to describe, it isn't real." },
];

const lookFor = [
  { name: "MENA focus", desc: "Operating in or targeting the region (a MENA name / domain counts even when ops aren't verifiable online)." },
  { name: "Deep technology", desc: "Proprietary, defensible tech at the core — not a thin layer over commodity APIs." },
  { name: "Strong IP / moat", desc: "Patents, proprietary data flywheel, hardware, or hard-won regulatory position." },
  { name: "Team", desc: "Domain depth and a track record that matches the company's claimed technical ambition." },
  { name: "Stage", desc: "Pre-seed to Series A." },
  { name: "Model fit", desc: "Sells a technical product / API / hardware / regulated service — not a pure marketplace or basic SaaS." },
];

const rejects = [
  "Pure marketplace, agency, services, commodity SaaS, or real estate with no deep-tech layer",
  "Repeats the failure mechanism of a past MIXED / NO / write-off bet",
  "Credibility red flags — unverifiable or mismatched founder identity, no verifiable existence",
  "Outside the pre-seed → Series A window, or no MENA connection at all",
];

export default function FrameworkPage() {
  return (
    <div className="p-4 sm:p-6 max-w-3xl space-y-8">
      <div>
        <h1 className="text-lg font-semibold text-foreground mb-1">Investment Framework</h1>
        <p className="text-xs text-muted-foreground">
          Raed Ventures · Sector-agnostic early-stage deep tech, MENA focus
        </p>
      </div>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-2">How the AI decides</h2>
        <div className="bg-card border border-border rounded-xl p-4 text-xs text-muted-foreground leading-relaxed space-y-2">
          <p>
            The recommendation is a <span className="text-foreground font-medium">pattern-based judgment</span>, not a
            weighted score. For each new lead the AI retrieves the most similar companies from{" "}
            <span className="text-foreground">Raed's portfolio retrospective</span> (42 past bets, each labeled with its
            original thesis, kill criterion, what actually happened, and a hindsight verdict), then reasons by analogy.
          </p>
          <p>
            It tests the new deal against those precedents' <span className="text-foreground">kill criteria</span>, applies
            the named mental models, and weights the <span className="text-foreground">disagreement cases</span> most
            heavily — the bets where our original rationale and reality diverged are where we've learned what we
            systematically mis-underwrite.
          </p>
          <p className="text-muted-foreground">
            Core discipline: <span className="text-foreground">decision quality is separate from outcome.</span> The AI
            judges whether the signals knowable now fit the patterns of theses that held up — not whether a company
            happened to succeed.
          </p>
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3">The three buckets</h2>
        <div className="space-y-2">
          {buckets.map((b) => (
            <div key={b.label} className={`bg-card border ${b.border} rounded-xl p-4`}>
              <div className="flex items-baseline gap-3">
                <span className={`text-sm font-bold ${b.color}`}>{b.label}</span>
                <span className="text-sm font-medium text-foreground">{b.head}</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{b.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3">Raed's three filters</h2>
        <div className="space-y-2">
          {filters.map((f) => (
            <div key={f.name} className="bg-card border border-border rounded-xl p-4">
              <p className="text-sm font-medium text-primary">{f.name}</p>
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3">What we look for</h2>
        <div className="grid grid-cols-2 gap-2">
          {lookFor.map((c) => (
            <div key={c.name} className="bg-card border border-border rounded-xl p-3">
              <p className="text-xs font-medium text-foreground">{c.name}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">{c.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3">Clear rejects</h2>
        <div className="bg-card border border-error/40 rounded-xl p-4 space-y-2">
          {rejects.map((r) => (
            <div key={r} className="flex gap-2 text-xs text-error">
              <span className="shrink-0 mt-0.5">✗</span>
              <span className="leading-relaxed">{r}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-foreground mb-2">It learns from you</h2>
        <div className="bg-card border border-border rounded-xl p-4 text-xs text-muted-foreground leading-relaxed">
          Every <span className="text-success">👍</span> / <span className="text-warning">👎</span> and every
          bucket you override is recorded with your reason. Those become the labeled patterns we feed back in to keep
          the model calibrated to Raed's judgement over time.
        </div>
      </section>
    </div>
  );
}
