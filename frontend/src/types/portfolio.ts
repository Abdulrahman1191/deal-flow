export type Decision = "FUNDED" | "PASSED" | "NOT_SEEN";
export type OutcomeStatus =
  | "exited"
  | "growing"
  | "stalled"
  | "zombie"
  | "failed"
  | "acqui_hire"
  | "too_early";
export type Direction = "POSITIVE" | "NEGATIVE";

export interface PortfolioCompany {
  id: string;
  name: string;
  sector: string | null;
  region: string | null;
  founder_names: string[] | null;
  website: string | null;
  description: string | null;
  our_decision: Decision;
  decision_at: string | null;
  decision_rationale: string | null;
  invested_amount_usd: number | null;
  current_status: OutcomeStatus;
  last_reviewed_at: string | null;
  created_at: string;
  signal_count: number;
  outcome_count: number;
}

export interface PortfolioOutcome {
  id: string;
  status: OutcomeStatus;
  recorded_at: string;
  current_valuation_usd: number | null;
  last_round_stage: string | null;
  notes: string | null;
}

export interface PortfolioSignal {
  id: string;
  signal_type: string;
  direction: Direction;
  weight: number;
  observed_at: string | null;
  note: string | null;
  created_at: string;
}

export interface PortfolioCompanyDetail extends PortfolioCompany {
  outcomes: PortfolioOutcome[];
  signals: PortfolioSignal[];
}

export interface PortfolioVocab {
  decisions: Decision[];
  outcomes: OutcomeStatus[];
  signal_types: string[];
  directions: Direction[];
}
