import { useEffect, useRef, useState } from "react";
import {
  DATE_PRESET_OPTIONS,
  LEAD_SOURCE_OPTIONS,
  datePresetFilter,
  leadSourceLabel,
  type DatePreset,
  type LeadSource,
} from "../../lib/leadFilters";

interface Props {
  source: LeadSource[];
  onSourceChange: (source: LeadSource[]) => void;
  appliedFrom: string | null;
  appliedTo: string | null;
  datePreset: DatePreset | "custom" | null;
  onDateChange: (next: {
    appliedFrom: string | null;
    appliedTo: string | null;
    datePreset: DatePreset | "custom" | null;
  }) => void;
}

const CLEAR_DATE = { appliedFrom: null, appliedTo: null, datePreset: null };

function dateChipLabel(
  appliedFrom: string | null,
  appliedTo: string | null,
  datePreset: DatePreset | "custom" | null,
): string {
  if (datePreset && datePreset !== "custom") {
    return DATE_PRESET_OPTIONS.find((o) => o.value === datePreset)?.label ?? "Custom range";
  }
  if (appliedFrom && appliedTo) return `${appliedFrom} – ${appliedTo}`;
  if (appliedFrom) return `From ${appliedFrom}`;
  if (appliedTo) return `Until ${appliedTo}`;
  return "Custom range";
}

const controlClass =
  "bg-card border border-border rounded-lg px-3 py-2 text-sm text-foreground hover:bg-muted transition-colors focus:outline-none focus:border-ring";

export default function LeadsFilterBar({
  source,
  onSourceChange,
  appliedFrom,
  appliedTo,
  datePreset,
  onDateChange,
}: Props) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState(appliedFrom ?? "");
  const [customTo, setCustomTo] = useState(appliedTo ?? "");
  const sourceRef = useRef<HTMLDivElement>(null);
  const dateRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setCustomFrom(appliedFrom ?? "");
    setCustomTo(appliedTo ?? "");
  }, [appliedFrom, appliedTo]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (sourceRef.current && !sourceRef.current.contains(e.target as Node)) setSourceOpen(false);
      if (dateRef.current && !dateRef.current.contains(e.target as Node)) setDateOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const hasDate = !!(appliedFrom || appliedTo);
  const hasAnyActive = source.length > 0 || hasDate;

  const toggleSource = (value: LeadSource) => {
    onSourceChange(source.includes(value) ? source.filter((s) => s !== value) : [...source, value]);
  };

  const applyCustomRange = () => {
    onDateChange({
      appliedFrom: customFrom || null,
      appliedTo: customTo || null,
      datePreset: "custom",
    });
    setDateOpen(false);
  };

  const clearAll = () => {
    onSourceChange([]);
    onDateChange(CLEAR_DATE);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div ref={dateRef} className="relative">
        <button
          type="button"
          onClick={() => setDateOpen((v) => !v)}
          className={`${controlClass} ${hasDate ? "border-ring text-foreground" : ""}`}
        >
          {hasDate ? `Applied: ${dateChipLabel(appliedFrom, appliedTo, datePreset)}` : "Applied"}
        </button>
        {dateOpen && (
          <div className="absolute left-0 z-20 mt-1 w-64 max-w-[90vw] bg-card border border-border rounded-lg shadow-md p-3 space-y-3">
            <div className="flex flex-col gap-1">
              {DATE_PRESET_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    onDateChange(datePresetFilter(opt.value));
                    setDateOpen(false);
                  }}
                  className={`text-left px-2 py-1.5 text-sm rounded-md hover:bg-muted transition-colors ${
                    datePreset === opt.value ? "text-primary font-medium" : "text-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="border-t border-border pt-3 space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Custom range</p>
              <div className="flex items-center gap-2">
                <input
                  type="date"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  aria-label="Applied from"
                  className="w-full bg-background border border-border rounded-md px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
                />
                <span className="text-muted-foreground text-xs">to</span>
                <input
                  type="date"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  aria-label="Applied to"
                  className="w-full bg-background border border-border rounded-md px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
                />
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    onDateChange(CLEAR_DATE);
                    setDateOpen(false);
                  }}
                  className="px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={applyCustomRange}
                  disabled={!customFrom && !customTo}
                  className="px-2.5 py-1 text-xs font-medium rounded-md bg-primary text-white hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div ref={sourceRef} className="relative">
        <button
          type="button"
          onClick={() => setSourceOpen((v) => !v)}
          className={`${controlClass} ${source.length > 0 ? "border-ring text-foreground" : ""}`}
        >
          {source.length > 0 ? `Source (${source.length})` : "Source"}
        </button>
        {sourceOpen && (
          <div className="absolute left-0 z-20 mt-1 w-56 max-w-[90vw] bg-card border border-border rounded-lg shadow-md p-3 space-y-1">
            {LEAD_SOURCE_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className="flex items-center gap-2 px-1 py-1.5 text-sm text-foreground rounded-md hover:bg-muted transition-colors cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={source.includes(opt.value)}
                  onChange={() => toggleSource(opt.value)}
                  className="h-3.5 w-3.5 rounded border-border accent-primary"
                />
                {opt.label}
              </label>
            ))}
            {source.length > 0 && (
              <button
                type="button"
                onClick={() => onSourceChange([])}
                className="mt-1 px-1 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        )}
      </div>

      {hasAnyActive && (
        <div className="flex flex-wrap items-center gap-1.5">
          {source.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-1 pl-2.5 pr-1.5 py-1 rounded-full bg-primary/10 text-primary text-xs font-medium border border-primary/30"
            >
              {leadSourceLabel(s)}
              <button
                type="button"
                onClick={() => toggleSource(s)}
                aria-label={`Remove ${leadSourceLabel(s)} filter`}
                className="hover:opacity-70"
              >
                &times;
              </button>
            </span>
          ))}
          {hasDate && (
            <span className="inline-flex items-center gap-1 pl-2.5 pr-1.5 py-1 rounded-full bg-primary/10 text-primary text-xs font-medium border border-primary/30">
              Applied: {dateChipLabel(appliedFrom, appliedTo, datePreset)}
              <button
                type="button"
                onClick={() => onDateChange(CLEAR_DATE)}
                aria-label="Remove applied date filter"
                className="hover:opacity-70"
              >
                &times;
              </button>
            </span>
          )}
          <button
            type="button"
            onClick={clearAll}
            className="px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}
