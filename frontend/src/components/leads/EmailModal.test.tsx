// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EmailModal from "./EmailModal";
import type { Assessment, Lead } from "../../types/lead";
import type { MyReasons } from "../../api/overrides";

const regenerateDraft = vi.fn().mockResolvedValue({
  draft_type: "rejection",
  draft_subject: "Subject",
  draft_body: "Body",
});

vi.mock("../../api/assessments", () => ({
  regenerateDraft: (...args: unknown[]) => regenerateDraft(...args),
  sendEmail: vi.fn(),
  updateDraft: vi.fn(),
}));

const fetchMyReasons = vi.fn<[], Promise<MyReasons>>().mockResolvedValue({
  rating_up: [],
  rating_down: [],
  bucket_yes: [],
  bucket_maybe: [],
  bucket_reject: [],
});

vi.mock("../../api/overrides", () => ({
  fetchMyReasons: () => fetchMyReasons(),
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
});

describe("EmailModal rejection reason chips", () => {
  it("drops free-text reasons seeded on the card so they don't consume the 3-chip cap", async () => {
    renderModal({
      // "Weak founder-market fit" mirrors a FeedbackModal tag stored via the
      // rate-down auto-reject path (issue #225) -- it renders no chip here.
      rejection_reasons: ["Weak founder-market fit", "Founder(s)", "Market size"],
    });
    await waitFor(() => expect(fetchMyReasons).toHaveBeenCalled());

    expect(screen.queryByText("Weak founder-market fit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reason-limit-notice")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Lack of traction"));

    expect(screen.getByTestId("reason-limit-notice")).toBeInTheDocument();
  });

  it("filters learned (non-canonical) chips out of the regenerate-draft request and flags them as not shaping the draft", async () => {
    fetchMyReasons.mockResolvedValueOnce({
      rating_up: [],
      rating_down: [],
      bucket_yes: [],
      bucket_maybe: [],
      bucket_reject: [{ text: "Not deep tech / wrong model", count: 3, last_used_at: "2026-01-01T00:00:00Z", source: "personal" }],
    });
    renderModal();

    const learnedChip = await screen.findByText("Not deep tech / wrong model");
    fireEvent.click(learnedChip);
    fireEvent.click(screen.getByText("Market size"));

    expect(screen.getByTestId("learned-reason-note")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("regenerate-with-reasons-btn"));

    await waitFor(() => expect(regenerateDraft).toHaveBeenCalled());
    expect(regenerateDraft).toHaveBeenCalledWith("lead-1", ["Market size"]);
  });
});
