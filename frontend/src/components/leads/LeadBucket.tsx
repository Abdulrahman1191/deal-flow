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
        <span className={`w-2 h-2 rounded-full ${accent}`} />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <span className="ml-auto text-xs text-muted-foreground">{leads.length}</span>
      </div>
      {leads.length === 0 && (
        <div className="text-xs text-muted-foreground text-center py-8 border border-dashed border-border rounded-xl">
          No leads
        </div>
      )}
      {leads.map((lead) => (
        <LeadCard key={lead.id} lead={lead} />
      ))}
    </div>
  );
}
