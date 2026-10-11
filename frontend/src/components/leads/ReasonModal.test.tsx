// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReasonModal from "./ReasonModal";
import type { MyReasons } from "../../api/overrides";

const fetchMyReasons = vi.fn<[], Promise<MyReasons>>().mockResolvedValue({
  rating_up: [],
  rating_down: [],
  bucket_yes: [],
  bucket_maybe: [],
  bucket_reject: [{ text: "Already covered this space", count: 3, last_used_at: "2026-01-01T00:00:00Z", source: "personal" }],
});

vi.mock("../../api/overrides", () => ({
  fetchMyReasons: () => fetchMyReasons(),
}));

function renderModal(onSubmit = vi.fn()) {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <ReasonModal bucket="REJECT" companyName="Acme Robotics" onSubmit={onSubmit} onCancel={vi.fn()} />
    </QueryClientProvider>,
  );
  return onSubmit;
}

afterEach(() => {
  cleanup();
  fetchMyReasons.mockClear();
});

// issue #243/#244: the "Your reasons" free-text learned-phrase picker and the
// optional note textarea were removed from EmailModal's rendering, but the
// capture path they feed -- this modal's onSubmit, which still reaches
// POST /assessments/:id/override and ultimately `rejection_reasons` on the
// card -- must stay untouched. Locking it down here so a future EmailModal
// change can't silently break capture while only the rendering moves.
describe("ReasonModal free-text capture path", () => {
  it("captures a free-text note with no tags selected", () => {
    const onSubmit = renderModal();

    fireEvent.change(screen.getByPlaceholderText(/Anything specific to remember/), {
      target: { value: "Founder ghosted after term sheet" },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(onSubmit).toHaveBeenCalledWith({
      reason_tags: [],
      reason: "Founder ghosted after term sheet",
    });
  });

  it("captures selected quick-reason tags alongside the free-text note", () => {
    const onSubmit = renderModal();

    fireEvent.click(screen.getByText("Not MENA"));
    fireEvent.change(screen.getByPlaceholderText(/Anything specific to remember/), {
      target: { value: "Based in the US" },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(onSubmit).toHaveBeenCalledWith({
      reason_tags: ["Not MENA"],
      reason: "Based in the US",
    });
  });

  it("folds a selected learned reason into the combined `reason` string", async () => {
    const onSubmit = renderModal();

    await waitFor(() => expect(screen.getByText("Already covered this space")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Already covered this space"));
    fireEvent.click(screen.getByText("Save"));

    expect(onSubmit).toHaveBeenCalledWith({
      reason_tags: [],
      reason: "Already covered this space",
    });
  });

  it("disables Save until a tag, note, or learned reason is picked", () => {
    renderModal();

    expect(screen.getByText("Save")).toBeDisabled();
    expect(screen.getByText("Pick a tag or add a note")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Not MENA"));

    expect(screen.getByText("Save")).not.toBeDisabled();
  });
});
