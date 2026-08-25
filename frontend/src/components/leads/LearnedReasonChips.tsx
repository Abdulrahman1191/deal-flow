import type { LearnedReason } from "../../api/overrides";

interface Props {
  reasons: LearnedReason[];
  selected: Set<string>;
  onToggle: (text: string) => void;
  /** Classes applied to a chip while selected — matches the modal's accent. */
  activeClassName: string;
}

/**
 * One-click reason chips learned from the user's own past notes/tags
 * (issue #152). Renders nothing when there's nothing learned yet — that IS
 * the empty state, so the modal never shows a placeholder box for a feature
 * a brand-new user hasn't triggered.
 */
export default function LearnedReasonChips({ reasons, selected, onToggle, activeClassName }: Props) {
  if (reasons.length === 0) return null;

  return (
    <div>
      <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-2">
        Your reasons
      </label>
      <div className="flex flex-wrap gap-2">
        {reasons.map((r) => {
          const isOn = selected.has(r.text);
          return (
            <button
              key={r.text}
              type="button"
              onClick={() => onToggle(r.text)}
              title={r.source === "team" ? "Common reason used by the team" : undefined}
              className={`text-xs px-3 py-1.5 rounded-full transition-colors border ${
                isOn ? activeClassName : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
              }`}
            >
              {r.text}
              {r.source === "team" && <span className="ml-1 opacity-60">· team</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
