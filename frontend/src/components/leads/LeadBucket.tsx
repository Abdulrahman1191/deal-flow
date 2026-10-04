import type { Lead } from "../../types/lead";
import LeadCard from "./LeadCard";

interface Props {
  title: string;
  leads: Lead[];
  accent: string;
  /** Bulk-select (issue #141) — omitted entirely hides checkboxes on cards.
   * Read-only-while-impersonating is handled inside LeadCard itself (it
   * already reads viewAs from the store). */
  selected?: Set<string>;
  onToggleSelect?: (id: string) => void;
  /** Select-all-in-column (issue #204) — e.g. "re-run all my MAYBEs" without
   * having to click every card. Omitted entirely hides the control. */
  onSelectAllInColumn?: () => void;
}

export default function LeadBucket({
  title,
  leads,
  accent,
  selected,
  onToggleSelect,
  onSelectAllInColumn,
}: Props) {
  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center gap-2 mb-1">
        <span className={`w-2.5 h-2.5 rounded-full ${accent}`} />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <div className="ml-auto flex items-center gap-2">
          <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-muted px-2 text-xs font-semibold text-muted-foreground">
            {leads.length}
          </span>
          {onSelectAllInColumn && leads.length > 0 && (
            <button
              onClick={onSelectAllInColumn}
              data-testid="select-all-column-btn"
              className="px-2 py-1 text-xs font-medium rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              {`Select all (${leads.length})`}
            </button>
          )}
        </div>
      </div>
      {leads.length === 0 && (
        <div className="text-xs text-muted-foreground text-center py-10 border border-dashed border-border rounded-2xl bg-card/40">
          No leads
        </div>
      )}
      {leads.map((lead, i) => (
        <LeadCard
          key={lead.id}
          lead={lead}
          index={i}
          selected={selected?.has(lead.id)}
          onToggleSelect={onToggleSelect ? () => onToggleSelect(lead.id) : undefined}
        />
      ))}
    </div>
  );
}
