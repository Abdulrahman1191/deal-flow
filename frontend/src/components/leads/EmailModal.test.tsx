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

describe("EmailModal rejection reason chips", () => {
  it("drops free-text reasons seeded on the card so they don't consume the 3-chip cap", () => {
    renderModal({
      // "Weak founder-market fit" mirrors a FeedbackModal tag stored via the
      // rate-down auto-reject path (issue #225) -- it renders no chip here.
      rejection_reasons: ["Weak founder-market fit", "Founder(s)", "Market size"],
    });

    expect(screen.queryByText("Weak founder-market fit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reason-limit-notice")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Lack of traction"));

    expect(screen.getByTestId("reason-limit-notice")).toBeInTheDocument();
  });

  // issue #243: the historical free-text "Your reasons" picker was removed
  // from this modal, but the capture path it used to read from (rejection
  // reasons seeded on the card, filtered to canonical labels) must still
  // flow through to the outgoing request untouched.
  it("still seeds the canonical selection from historical rejection_reasons and sends it to the template endpoint", async () => {
    renderModal({ rejection_reasons: ["Market size"] });

    expect(screen.getByText("Market size").closest("button")).toHaveClass("bg-error/20");

    fireEvent.click(screen.getByTestId("render-template-btn"));

    await waitFor(() =>
      expect(renderRejectionTemplate).toHaveBeenCalledWith("lead-1", { reasons: ["Market size"], language: undefined }),
    );
  });

  // issue #243: "Your reasons" (the free-text learned-phrase picker) is
  // deleted from this modal entirely -- only the canonical chips remain.
  it("no longer renders the 'Your reasons' learned-phrase block", () => {
    renderModal();

    expect(screen.queryByText("Your reasons")).not.toBeInTheDocument();
    expect(screen.queryByTestId("learned-reason-note")).not.toBeInTheDocument();
  });

  // issue #243: the reason-chip row is capped and scrolls internally so it
  // can never again push Subject/Body below the fold.
  it("caps the reason-chip row's height so it scrolls internally instead of growing", () => {
    renderModal();

    const chipRow = screen.getByTestId("reason-chips-scroll");
    expect(chipRow.className).toMatch(/max-h-24/);
    expect(chipRow.className).toMatch(/overflow-y-auto/);
  });
});

// issue #244: at an 823px viewport, the body textarea's top must be above
// the fold with at least 200px visible on open. jsdom doesn't do real
// layout, so this can't assert rendered pixels -- instead it locks down the
// structural guarantees the geometry note's arithmetic relies on: every
// variable-height block above the textarea (reason-block notes, the
// stale-draft notice) is hard-capped rather than left to grow with content,
// and the textarea's own floor matches the 200px requirement.
describe("EmailModal body textarea fold guarantee (issue #244)", () => {
  it("floors the body textarea at 200px, not the old 140px", () => {
    renderModal();

    expect(screen.getByTestId("email-body-textarea").className).toMatch(/min-h-\[200px\]/);
  });

  it("caps the reason block's notes/actions region as a whole, not just the chip row", () => {
    renderModal({
      rejection_reasons: ["Founder(s)", "Dedication and focus", "Market size"],
    });

    const notesRegion = screen.getByTestId("reason-chips-scroll").parentElement;
    expect(notesRegion?.className).toMatch(/max-h-\[135px\]/);
    expect(notesRegion?.className).toMatch(/overflow-y-auto/);
  });

  it("caps the stale-draft notice so it can't grow unbounded above the textarea", async () => {
    renderModal({ draft_body: null, draft_subject: null, draft_type: null, draft_bucket: null });

    const notice = await screen.findByTestId("stale-draft-notice");
    expect(notice.className).toMatch(/max-h-\[60px\]/);
    expect(notice.className).toMatch(/overflow-y-auto/);
  });

  it("truncates a long company name instead of letting the header wrap to a second line", () => {
    renderModal();

    const name = screen.getByText("Acme Robotics");
    expect(name.className).toMatch(/truncate/);
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
