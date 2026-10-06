import { format, startOfYear, subDays } from "date-fns";

export type LeadSource = "email_inbox" | "website_form" | "other" | "unknown";

// Labels from the issue's own breakdown of the 1,143 open leads at the time
// of writing: email_inbox (823), website_form (219), other (77), unknown (24).
export const LEAD_SOURCE_OPTIONS: { value: LeadSource; label: string }[] = [
  { value: "email_inbox", label: "Email (info@raed.vc)" },
  { value: "website_form", label: "Website form" },
  { value: "other", label: "Other" },
  { value: "unknown", label: "Unknown" },
];

export function leadSourceLabel(value: string): string {
  return LEAD_SOURCE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export type DatePreset = "7d" | "30d" | "90d" | "year";

export const DATE_PRESET_OPTIONS: { value: DatePreset; label: string }[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "year", label: "This year" },
];

const toIsoDate = (d: Date) => format(d, "yyyy-MM-dd");

/** Inclusive day range for a preset, e.g. "7d" = today and the 6 days before it. */
export function dateRangeForPreset(preset: DatePreset): { from: string; to: string } {
  const today = new Date();
  const to = toIsoDate(today);
  switch (preset) {
    case "7d":
      return { from: toIsoDate(subDays(today, 6)), to };
    case "30d":
      return { from: toIsoDate(subDays(today, 29)), to };
    case "90d":
      return { from: toIsoDate(subDays(today, 89)), to };
    case "year":
      return { from: toIsoDate(startOfYear(today)), to };
  }
}

export interface LeadFiltersState {
  source: LeadSource[];
  appliedFrom: string | null;
  appliedTo: string | null;
  datePreset: DatePreset | "custom" | null;
}

export const DEFAULT_LEAD_FILTERS: LeadFiltersState = {
  source: [],
  appliedFrom: null,
  appliedTo: null,
  datePreset: null,
};

const STORAGE_KEY = "leadsBoardFilters";

function isLeadSource(value: unknown): value is LeadSource {
  return typeof value === "string" && LEAD_SOURCE_OPTIONS.some((o) => o.value === value);
}

/** Per-viewer convenience only — wrapped in try/catch since localStorage can
 * throw in private windows (issue #218). Falls back to no filters rather
 * than failing the board load. */
export function loadLeadFilters(): LeadFiltersState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LEAD_FILTERS;
    const parsed = JSON.parse(raw) as Partial<LeadFiltersState>;
    return {
      source: Array.isArray(parsed.source) ? parsed.source.filter(isLeadSource) : [],
      appliedFrom: typeof parsed.appliedFrom === "string" ? parsed.appliedFrom : null,
      appliedTo: typeof parsed.appliedTo === "string" ? parsed.appliedTo : null,
      datePreset: (parsed.datePreset as LeadFiltersState["datePreset"]) ?? null,
    };
  } catch {
    return DEFAULT_LEAD_FILTERS;
  }
}

export function saveLeadFilters(filters: LeadFiltersState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
  } catch {
    // Private window or storage disabled — filters just won't survive reload.
  }
}
