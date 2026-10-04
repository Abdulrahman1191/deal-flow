interface Props {
  breakdown: Record<string, { score: number; reasoning: string }>;
}

// Maxima per the 6-criterion rubric in claude_agent.py — they are not equal
// (MENA is out of 20, model fit out of 5), so each bar must be scaled
// against its own criterion's max, never a shared scale.
const CRITERIA: { key: string; label: string; max: number }[] = [
  { key: "deep_tech", label: "Deep tech", max: 25 },
  { key: "strong_ip", label: "IP", max: 20 },
  { key: "mena_focus", label: "MENA", max: 20 },
  { key: "team_experience", label: "Team", max: 20 },
  { key: "stage_alignment", label: "Stage", max: 10 },
  { key: "model_fit", label: "Model fit", max: 5 },
];

export default function ScoringStrip({ breakdown }: Props) {
  return (
    <div className="flex items-end gap-3" data-testid="scoring-strip">
      {CRITERIA.map(({ key, label, max }) => {
        const entry = breakdown[key];
        if (!entry) return null;
        const pct = Math.max(0, Math.min(100, (entry.score / max) * 100));
        return (
          <div
            key={key}
            className="flex flex-col items-center gap-1 w-9"
            title={`${label}: ${entry.score}/${max} — ${entry.reasoning}`}
            data-testid={`scoring-bar-${key}`}
          >
            <div className="h-8 w-1.5 bg-muted rounded-full overflow-hidden flex items-end">
              <div className="w-full bg-primary rounded-full" style={{ height: `${pct}%` }} />
            </div>
            <span className="text-[10px] text-foreground font-medium">{entry.score}</span>
            <span className="text-[9px] text-muted-foreground leading-tight text-center">
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
