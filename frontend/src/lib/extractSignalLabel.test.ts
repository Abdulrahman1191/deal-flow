import { describe, expect, it } from "vitest";
import { extractSignalLabel } from "./extractSignalLabel";

describe("extractSignalLabel", () => {
  it("splits on an em dash delimiter", () => {
    const { label, detail } = extractSignalLabel(
      "Founder identity unverified — the only contact we have is an unverified CRM entry, not a confirmed founder.",
    );
    expect(label).toBe("Founder identity unverified");
    expect(detail).toBe(
      "Founder identity unverified — the only contact we have is an unverified CRM entry, not a confirmed founder.",
    );
  });

  it("splits on an en dash delimiter", () => {
    const { label } = extractSignalLabel(
      "Instrument mismatch – they are raising a SAFE but describe terms like a priced round.",
    );
    expect(label).toBe("Instrument mismatch");
  });

  it("splits on a colon delimiter", () => {
    const { label } = extractSignalLabel(
      "Stage fits Raed's window: pre-seed with a working prototype and early revenue.",
    );
    expect(label).toBe("Stage fits Raed's window");
  });

  it("splits on an opening parenthesis delimiter", () => {
    const { label } = extractSignalLabel(
      "Strong IP position (3 granted patents covering the core sensing method).",
    );
    expect(label).toBe("Strong IP position");
  });

  it("splits on a period delimiter", () => {
    const { label } = extractSignalLabel(
      "No deck on file. Scored from the company website and public description alone.",
    );
    expect(label).toBe("No deck on file");
  });

  it("falls back to the first 60 characters with an ellipsis when there is no clean break", () => {
    const text =
      "This entire sentence runs on with no punctuation anywhere near the start so there is nothing to split on at all";
    const { label } = extractSignalLabel(text);
    expect(label.endsWith("…")).toBe(true);
    expect(label.length).toBeLessThanOrEqual(61);
  });

  it("never splits mid-word in the fallback path", () => {
    const text =
      "Supercalifragilisticexpialidocious is a word that keeps going past the sixty character fallback boundary without any delimiter";
    const { label } = extractSignalLabel(text);
    const withoutEllipsis = label.replace(/…$/, "");
    // Every word in the label must appear whole in the source text, bounded
    // by a space or the start/end of the string on both sides.
    for (const word of withoutEllipsis.split(" ").filter(Boolean)) {
      expect(text.includes(word)).toBe(true);
    }
    expect(text.startsWith(withoutEllipsis.trimEnd())).toBe(true);
    expect(text[withoutEllipsis.length] === " " || withoutEllipsis.length === text.length).toBe(
      true,
    );
  });

  it("rejects a delimiter prefix shorter than 8 characters and falls back", () => {
    // "No IP" before the ". " delimiter is only 5 characters.
    const text =
      "No IP. Competitors are aggressive and well-funded, which limits defensibility over time.";
    const { label } = extractSignalLabel(text);
    expect(label).not.toBe("No IP");
  });

  it("rejects a delimiter prefix longer than 70 characters and falls back", () => {
    const longPrefix = "a".repeat(80);
    const text = `${longPrefix}: short detail after it`;
    const { label } = extractSignalLabel(text);
    expect(label).not.toBe(longPrefix);
    expect(label.endsWith("…")).toBe(true);
  });

  it("strips trailing punctuation from the label", () => {
    const { label } = extractSignalLabel("Clear market need: strong demand signals from pilots.");
    expect(label.endsWith(":")).toBe(false);
    expect(label).toBe("Clear market need");
  });

  it("handles an Arabic string without mangling it", () => {
    const text =
      "الفريق المؤسس غير موثق لأن جهة الاتصال الوحيدة المسجلة في النظام هي شخص لم يتم تأكيد كونه أحد المؤسسين الفعليين للشركة";
    const { label, detail } = extractSignalLabel(text);
    expect(detail).toBe(text);
    expect(label.length).toBeGreaterThan(0);
    // No delimiter in the Arabic text, so this exercises the fallback path —
    // the label must be a clean prefix of the original, never a mid-word cut.
    const withoutEllipsis = label.replace(/…$/, "");
    expect(text.startsWith(withoutEllipsis)).toBe(true);
    const nextChar = text[withoutEllipsis.length];
    expect(nextChar === undefined || nextChar === " ").toBe(true);
  });

  it("returns the full original string as the detail even when a label is derived", () => {
    const text = "Deep tech core: proprietary sensor fusion algorithm with 2 pending patents.";
    const { detail } = extractSignalLabel(text);
    expect(detail).toBe(text);
  });
});
