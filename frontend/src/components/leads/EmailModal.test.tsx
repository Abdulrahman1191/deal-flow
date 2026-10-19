// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EmailModal from "./EmailModal";
import type { Assessment, Lead } from "../../types/lead";

const regenerateDraft = vi.fn().mockResolvedValue({
  draft_type: "rejection",
  draft_subject: "Subject",
  draft_body: "Body",
});

const renderRejectionTemplate = vi.fn().mockResolvedValue({
  draft_type: "rejection",
  draft_subject: "Template subject",
  draft_body: "Template body",
  draft_language: "en",
});

vi.mock("../../api/assessments", () => ({
  regenerateDraft: (...args: unknown[]) => regenerateDraft(...args),
  renderRejectionTemplate: (...args: unknown[]) => renderRejectionTemplate(...args),
  sendEmail: vi.fn(),
  updateDraft: vi.fn(),
}));

const baseAssessment: Assessment = {
  id: "assessment-1",
  lead_id: "lead-1",
  bucket: "REJECT",
  confidence_score: 20,
  summary: "Not a fit.",
  positive_signals: null,
  red_flags: null,
  traction: [],
  data_gaps: null,
  scoring_breakdown: null,
  draft_subject: "Thanks for sharing",
  draft_body: "Thanks, but we're passing.",
  draft_type: "rejection",
  draft_bucket: "REJECT",
  draft_language: null,
  research_sources: null,
  assessed_without_deck: false,
  user_override: null,
  user_override_at: null,
  rejection_reasons: null,
  user_rating: null,
  user_rating_at: null,
  approved_at: null,
  sent_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const baseLead: Lead = {
  id: "lead-1",
  copper_id: null,
  company_name: "Acme Robotics",
  website: null,
  description: null,
  stage: null,
  region: "UAE",
  founder_names: null,
  linkedin_urls: null,
  company_linkedin_url: null,
  pitch_deck_filename: null,
  pitch_deck_ingested_at: null,
  pitch_deck_drive_id: null,
  prior_contact: null,
  prior_contact_count: null,
  prior_contact_last_at: null,
  status: "assessed",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  applied_at: null,
};

function renderModal(assessmentOverrides: Partial<Assessment> = {}) {
  const assessment = { ...baseAssessment, ...assessmentOverrides };
  const lead: Lead = { ...baseLead, assessment };
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <EmailModal lead={lead} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  regenerateDraft.mockClear();
  renderRejectionTemplate.mockClear();
});

describe("EmailModal layout (issue #243)", () => {
  it("never renders the duplicated 'Your reasons' block", () => {
    renderModal({
      rejection_reasons: ["Weak founder-market fit", "Founder(s)", "Market size"],
    });

    expect(screen.queryByText("Your reasons")).not.toBeInTheDocument();
  });

  it("gives the body textarea the remaining height instead of a fixed row count", () => {
    renderModal();

    const textarea = screen.getByDisplayValue("Thanks, but we're passing.");
    expect(textarea).not.toHaveAttribute("rows");
    expect(textarea.className).toContain("flex-1");
  });
});

describe("EmailModal reason picker (issue #247)", () => {
  it("renders exactly four reason buttons, none disabled, none active by default", () => {
    renderModal();

    const buttons = [
      screen.getByText("Market size"),
      screen.getByText("Conflict of interest"),
      screen.getByText("No clear traction"),
      screen.getByText("Not a fit right now"),
    ].map((el) => el.closest("button")!);

    expect(buttons).toHaveLength(4);
    for (const btn of buttons) {
      expect(btn).not.toBeDisabled();
      expect(btn).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("renders no retired UNQUAL_REASON_LABELS chips, lock markers, or Copper note", () => {
    renderModal({
      rejection_reasons: ["Weak founder-market fit", "Founder(s)", "Out of our region"],
    });

    expect(screen.queryByText("Founder(s)")).not.toBeInTheDocument();
    expect(screen.queryByText("Out of our region")).not.toBeInTheDocument();
    expect(screen.queryByText("Weak founder-market fit")).not.toBeInTheDocument();
    expect(screen.queryByText(/Pass reasons/)).not.toBeInTheDocument();
    expect(screen.queryByText(/🔒/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("reason-limit-notice")).not.toBeInTheDocument();
    expect(screen.queryByTestId("internal-only-note")).not.toBeInTheDocument();
    expect(screen.queryByText(/Copper's Unqualification Reasons/)).not.toBeInTheDocument();
  });

  it("is single-select: picking another button switches, picking the active one clears it", () => {
    renderModal();

    const marketSize = screen.getByText("Market size").closest("button")!;
    const traction = screen.getByText("No clear traction").closest("button")!;

    fireEvent.click(marketSize);
    expect(marketSize).toHaveAttribute("aria-pressed", "true");
    expect(traction).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(traction);
    expect(marketSize).toHaveAttribute("aria-pressed", "false");
    expect(traction).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(traction);
    expect(traction).toHaveAttribute("aria-pressed", "false");
  });

  it("'Regenerate with AI' sends the single selected canonical reason", async () => {
    renderModal();

    fireEvent.click(screen.getByText("Market size"));
    fireEvent.click(screen.getByTestId("regenerate-with-reasons-btn"));

    await waitFor(() => expect(regenerateDraft).toHaveBeenCalled());
    expect(regenerateDraft).toHaveBeenCalledWith("lead-1", ["Market size"]);
  });
});

describe("EmailModal portal (issue #247)", () => {
  it("renders outside a transformed ancestor, directly under document.body", () => {
    const assessment = { ...baseAssessment };
    const lead: Lead = { ...baseLead, assessment };
    const queryClient = new QueryClient();

    render(
      <div data-testid="transformed-ancestor" style={{ transform: "translateY(8px)" }}>
        <QueryClientProvider client={queryClient}>
          <EmailModal lead={lead} onClose={() => {}} />
        </QueryClientProvider>
      </div>,
    );

    const ancestor = screen.getByTestId("transformed-ancestor");
    const overlay = screen.getByTestId("email-modal-overlay");

    // A transformed ancestor becomes the containing block for a
    // `position: fixed` descendant. jsdom doesn't run layout, so this
    // asserts the structural invariant that rules the bug out entirely:
    // the overlay has no transformed ancestor to begin with, because it's
    // portaled straight onto document.body.
    expect(ancestor.contains(overlay)).toBe(false);
    expect(overlay.parentElement).toBe(document.body);
  });
});

describe("EmailModal rejection template (issue #232)", () => {
  it("renders the stale draft from the zero-LLM template, not the AI path", async () => {
    renderModal({ draft_body: null, draft_subject: null, draft_type: null, draft_bucket: null });

    await waitFor(() => expect(renderRejectionTemplate).toHaveBeenCalled());
    expect(regenerateDraft).not.toHaveBeenCalled();
    expect(renderRejectionTemplate).toHaveBeenCalledWith("lead-1", { reasons: [], language: undefined });
  });

  it("'Use template with these reasons' calls the template endpoint, not the AI one", async () => {
    renderModal();

    fireEvent.click(screen.getByText("Market size"));
    fireEvent.click(screen.getByTestId("render-template-btn"));

    await waitFor(() => expect(renderRejectionTemplate).toHaveBeenCalled());
    expect(renderRejectionTemplate).toHaveBeenCalledWith("lead-1", { reasons: ["Market size"], language: undefined });
    expect(regenerateDraft).not.toHaveBeenCalled();
  });

  it("the EN/AR toggle re-renders the template in the chosen language", async () => {
    renderModal();

    fireEvent.click(screen.getByTestId("language-toggle-ar"));

    await waitFor(() => expect(renderRejectionTemplate).toHaveBeenCalledWith("lead-1", { reasons: [], language: "ar" }));
  });

  it("'Regenerate with AI' stays available as a secondary action beside the template button", () => {
    renderModal();

    expect(screen.getByTestId("render-template-btn")).toBeInTheDocument();
    expect(screen.getByTestId("regenerate-with-reasons-btn")).toBeInTheDocument();
  });
});
