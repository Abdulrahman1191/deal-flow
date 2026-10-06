import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  dateRangeForPreset,
  datePresetFilter,
  leadSourceLabel,
  loadLeadFilters,
  saveLeadFilters,
} from "./leadFilters";

describe("dateRangeForPreset", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("last 7 days is a 7-day inclusive window ending today", () => {
    const { from, to } = dateRangeForPreset("7d");
    expect(to).toBe("2026-03-15");
    expect(from).toBe("2026-03-09");
  });

  it("last 30 days is a 30-day inclusive window ending today", () => {
    const { from, to } = dateRangeForPreset("30d");
    expect(to).toBe("2026-03-15");
    expect(from).toBe("2026-02-14");
  });

  it("this year starts on January 1st of the current year", () => {
    const { from, to } = dateRangeForPreset("year");
    expect(from).toBe("2026-01-01");
    expect(to).toBe("2026-03-15");
  });
});

describe("datePresetFilter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Regression test for issue #220: the preset button handler used to spread
  // dateRangeForPreset's { from, to } directly into LeadFiltersState, which
  // expects appliedFrom/appliedTo — so clicking a preset never actually
  // filtered anything.
  it("maps the preset's range onto appliedFrom/appliedTo, not from/to", () => {
    const result = datePresetFilter("7d");
    expect(result).toEqual({
      appliedFrom: "2026-03-09",
      appliedTo: "2026-03-15",
      datePreset: "7d",
    });
    expect(result).not.toHaveProperty("from");
    expect(result).not.toHaveProperty("to");
  });
});

// Tests run under vitest's default "node" environment, which has no global
// localStorage (that's a jsdom/browser API) — stand in with a minimal
// in-memory mock rather than pulling in a jsdom dependency for this alone.
function installLocalStorageMock() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    clear: () => store.clear(),
  });
}

describe("loadLeadFilters", () => {
  beforeEach(() => {
    installLocalStorageMock();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("re-derives a stale persisted preset's dates from today instead of trusting storage", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-15T12:00:00Z"));
    saveLeadFilters({
      source: [],
      appliedFrom: "2020-01-01",
      appliedTo: "2020-01-07",
      datePreset: "7d",
    });

    vi.setSystemTime(new Date("2026-04-01T12:00:00Z"));
    const restored = loadLeadFilters();

    expect(restored.datePreset).toBe("7d");
    expect(restored.appliedFrom).toBe("2026-03-26");
    expect(restored.appliedTo).toBe("2026-04-01");
  });

  it("trusts the stored dates for a custom range, which has no preset to re-derive from", () => {
    saveLeadFilters({
      source: [],
      appliedFrom: "2026-01-01",
      appliedTo: "2026-01-15",
      datePreset: "custom",
    });

    const restored = loadLeadFilters();

    expect(restored).toEqual({
      source: [],
      appliedFrom: "2026-01-01",
      appliedTo: "2026-01-15",
      datePreset: "custom",
    });
  });
});

describe("leadSourceLabel", () => {
  it("maps known source slugs to their display label", () => {
    expect(leadSourceLabel("email_inbox")).toBe("Email (info@raed.vc)");
    expect(leadSourceLabel("website_form")).toBe("Website form");
    expect(leadSourceLabel("other")).toBe("Other");
    expect(leadSourceLabel("unknown")).toBe("Unknown");
  });

  it("falls back to the raw value for an unrecognized slug", () => {
    expect(leadSourceLabel("something_new")).toBe("something_new");
  });
});
