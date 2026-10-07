import { describe, expect, it } from "vitest";
import { highlightMetrics } from "./highlightMetrics";

describe("highlightMetrics", () => {
  it("bolds a currency amount, a count-with-unit, and a percentage", () => {
    const out = highlightMetrics(
      "~$220K GMV, 550 vendors, 8 paid stores, 3,000+ registered users, 18% MoM growth",
    );
    expect(out).toContain("<strong>$220K</strong>");
    expect(out).toContain("<strong>550 vendors</strong>");
    expect(out).toContain("<strong>8 paid stores</strong>");
    expect(out).toContain("<strong>3,000+ registered users</strong>");
    expect(out).toContain("<strong>18%</strong>");
  });

  it("bolds a currency-code amount with a multiplier and a plus", () => {
    const out = highlightMetrics("SAR 1.5M+ in KSA grants, £1M+ UK government grant received in full");
    expect(out).toContain("<strong>SAR 1.5M+</strong>");
    expect(out).toContain("<strong>£1M+</strong>");
  });

  it("leaves plain prose with no numbers untouched", () => {
    const text = "The team has strong domain expertise in robotics and computer vision.";
    expect(highlightMetrics(text)).toBe(text);
  });

  it("does not bold a digit that is part of an alphanumeric token", () => {
    const out = highlightMetrics("Built on a fine-tuned GPT-4 model for clinical reasoning.");
    expect(out).not.toContain("<strong>");
  });

  it("escapes HTML-ish input before wrapping, so markup cannot be injected", () => {
    const out = highlightMetrics("<script>alert(1)</script> raised $5 from a friend");
    expect(out).toBe("&lt;script&gt;alert(1)&lt;/script&gt; raised <strong>$5</strong> from a friend");
    expect(out).not.toContain("<script>");
  });

  it("handles empty input without throwing", () => {
    expect(highlightMetrics("")).toBe("");
  });
});
