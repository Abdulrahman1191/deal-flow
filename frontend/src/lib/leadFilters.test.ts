import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dateRangeForPreset, leadSourceLabel } from "./leadFilters";

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
