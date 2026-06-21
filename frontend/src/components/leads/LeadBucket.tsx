import type { Lead } from "../../types/lead";
import LeadCard from "./LeadCard";

interface Props {
  title: string;
  leads: Lead[];
  accent: string;
}

export default function LeadBucket({ title, leads, accent }: Props) {
  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center gap-2 mb-1">
        <span className={`w-2.5 h-2.5 rounded-full ${accent}`} />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <span className="ml-auto flex h-6 min-w-6 items-center justify-center rounded-full bg-muted px-2 text-xs font-semibold text-muted-foreground">
          {leads.length}
        </span>
      </div>
      {leads.length === 0 && (
        <div className="text-xs text-muted-foreground text-center py-10 border border-dashed border-border rounded-2xl bg-card/40">
          No leads
        </div>
      )}
      {leads.map((lead, i) => (
        <LeadCard key={lead.id} lead={lead} index={i} />
      ))}
    </div>
  );
}
