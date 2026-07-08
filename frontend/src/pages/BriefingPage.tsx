import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchTodayBriefing, fetchBriefings, triggerBriefing } from "../api/briefings";
import BriefingPanel from "../components/briefing/BriefingPanel";
import BriefingArchive from "../components/briefing/BriefingArchive";
import { format } from "date-fns";
import client from "../api/client";
import type { Briefing } from "../types/briefing";

export default function BriefingPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: today, isLoading: todayLoading } = useQuery({
    queryKey: ["briefing-today"],
    queryFn: fetchTodayBriefing,
    retry: false,
  });

  const { data: selected } = useQuery({
    queryKey: ["briefing", selectedDate],
    queryFn: () => client.get<Briefing>(`/briefings/${selectedDate}`).then((r) => r.data),
    enabled: !!selectedDate,
  });

  const triggerMutation = useMutation({
    mutationFn: triggerBriefing,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["briefing-today"] }), 5000);
    },
  });

  const briefing = selectedDate ? selected : today;
  const displayDate = briefing ? format(new Date(briefing.date), "EEEE, dd/MM/yyyy") : null;

  return (
    <div className="flex flex-col md:flex-row h-full">
      <aside className="w-full md:w-52 shrink-0 border-b md:border-b-0 md:border-r border-border p-4 overflow-y-auto max-h-44 md:max-h-none">
        <button
          onClick={() => setSelectedDate(null)}
          className="w-full text-left px-3 py-2 rounded-lg text-sm font-medium text-foreground bg-muted mb-3"
        >
          Today
        </button>
        <BriefingArchive onSelect={setSelectedDate} selectedDate={selectedDate ?? undefined} />
      </aside>

      <main className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Daily Investment Briefing</h1>
            {displayDate && <p className="text-xs text-muted-foreground mt-0.5">{displayDate}</p>}
          </div>
          <button
            onClick={() => triggerMutation.mutate()}
            disabled={triggerMutation.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-primary hover:bg-primary/90 text-white disabled:opacity-50 transition-colors"
          >
            {triggerMutation.isPending ? "Generating…" : "Generate Now"}
          </button>
        </div>

        {todayLoading && !selectedDate && (
          <p className="text-muted-foreground text-sm animate-pulse">Loading briefing…</p>
        )}
        {!briefing && !todayLoading && !selectedDate && (
          <div className="text-center py-16 text-muted-foreground text-sm">
            No briefing yet for today. Click "Generate Now" to create one.
          </div>
        )}
        {!briefing && !todayLoading && selectedDate && (
          <div className="text-center py-16 text-muted-foreground text-sm">
            No briefing found for this date.
          </div>
        )}
        {briefing && <BriefingPanel briefing={briefing} />}
      </main>
    </div>
  );
}
