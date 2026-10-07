// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LeadCard from "./LeadCard";
import type { Assessment, Lead } from "../../types/lead";

const baseAssessment: Assessment = {
  id: "assessment-1",
  lead_id: "lead-1",
  bucket: "YES",
  confidence_score: 80,
  summary: "Strong team with deep tech moat.",
  positive_signals: null,
  red_flags: null,
  traction: [],
  data_gaps: null,
  scoring_breakdown: null,
  draft_subject: null,
  draft_body: null,
  draft_type: null,
  draft_bucket: null,
  research_sources: null,
  assessed_without_deck: false,
  user_override: null,
  user_override_at: null,
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

function renderCard(traction: string[] | undefined) {
  const assessment =
    traction === undefined
      ? (() => {
          const { traction: _omit, ...rest } = baseAssessment;
          return rest as unknown as Assessment;
        })()
      : { ...baseAssessment, traction };
  const lead: Lead = { ...baseLead, assessment };
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <LeadCard lead={lead} />
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("LeadCard traction row", () => {
  it("renders the chip row under the confidence bar, chips in order, when traction is evidenced", () => {
    renderCard(["$220K GMV", "550 vendors"]);

    const row = screen.getByTestId("traction-row");
    const chips = screen.getAllByTestId("traction-chip");
    expect(chips.map((c) => c.textContent)).toEqual(["$220K GMV", "550 vendors"]);
    expect(row).toContainElement(chips[0]);
    expect(row).toContainElement(chips[1]);
  });

  it("renders no row and no placeholder when traction is an empty array", () => {
    renderCard([]);

    expect(screen.queryByTestId("traction-row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("traction-chip")).not.toBeInTheDocument();
    expect(screen.queryByText(/no traction/i)).not.toBeInTheDocument();
  });

  it("renders no row and no placeholder when traction is absent", () => {
    renderCard(undefined);

    expect(screen.queryByTestId("traction-row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("traction-chip")).not.toBeInTheDocument();
    expect(screen.queryByText(/no traction/i)).not.toBeInTheDocument();
  });
});
