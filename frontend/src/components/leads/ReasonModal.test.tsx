// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReasonModal from "./ReasonModal";
import type { MyReasons } from "../../api/overrides";

const fetchMyReasons = vi.fn<[], Promise<MyReasons>>().mockResolvedValue({
  rating_up: [],
  rating_down: [],
  bucket_yes: [],
  bucket_maybe: [],
  bucket_reject: [{ text: "Known pattern match", count: 2, last_used_at: "2026-01-01T00:00:00Z", source: "personal" }],
});

vi.mock("../../api/overrides", () => ({
  fetchMyReasons: () => fetchMyReasons(),
}));

function renderModal(onSubmit = vi.fn()) {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <ReasonModal bucket="REJECT" companyName="Acme Robotics" onSubmit={onSubmit} onCancel={() => {}} />
    </QueryClientProvider>,
  );
  return onSubmit;
}

afterEach(() => {
  cleanup();
  fetchMyReasons.mockClear();
});

// The free-text reason capture path (issue #152) -- untouched by issue #243,
// which only removes this same data's *display* inside EmailModal. Locks
// down that quick tags, a learned chip, and a note still combine into the
// reason_tags/reason shape the rate/bucket-change callers persist to Copper.
describe("ReasonModal free-text reason capture", () => {
  it("combines a quick tag, a learned chip, and a note into reason_tags/reason", async () => {
    const onSubmit = renderModal();

    fireEvent.click(await screen.findByText("No deep tech"));
    fireEvent.click(await screen.findByText("Known pattern match"));
    fireEvent.change(screen.getByPlaceholderText("Anything specific to remember about this lead…"), {
      target: { value: "extra context" },
    });

    fireEvent.click(screen.getByText("Save"));

    expect(onSubmit).toHaveBeenCalledWith({
      reason_tags: ["No deep tech"],
      reason: "extra context; Known pattern match",
    });
  });

  it("disables Save until a tag, chip, or note is provided", () => {
    renderModal();

    expect(screen.getByText("Save")).toBeDisabled();
  });
});
