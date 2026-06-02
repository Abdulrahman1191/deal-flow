import client from "./client";
import type {
  PortfolioCompany,
  PortfolioCompanyDetail,
  PortfolioOutcome,
  PortfolioSignal,
  PortfolioVocab,
  Decision,
  OutcomeStatus,
  Direction,
} from "../types/portfolio";

export interface CompanyInput {
  name: string;
  sector?: string | null;
  region?: string | null;
  founder_names?: string[] | null;
  website?: string | null;
  description?: string | null;
  our_decision: Decision;
  decision_at?: string | null;
  decision_rationale?: string | null;
  invested_amount_usd?: number | null;
  initial_status?: OutcomeStatus;
}

export interface OutcomeInput {
  status: OutcomeStatus;
  current_valuation_usd?: number | null;
  last_round_stage?: string | null;
  notes?: string | null;
}

export interface SignalInput {
  signal_type: string;
  direction: Direction;
  weight: number;
  observed_at?: string | null;
  note?: string | null;
}

export const fetchVocab = () =>
  client.get<PortfolioVocab>("/portfolio/vocab").then((r) => r.data);

export const fetchCompanies = (params?: { decision?: Decision; status?: OutcomeStatus }) =>
  client
    .get<PortfolioCompany[]>("/portfolio/companies", { params })
    .then((r) => r.data);

export const fetchCompany = (id: string) =>
  client.get<PortfolioCompanyDetail>(`/portfolio/companies/${id}`).then((r) => r.data);

export const createCompany = (body: CompanyInput) =>
  client.post<PortfolioCompany>("/portfolio/companies", body).then((r) => r.data);

export const updateCompany = (id: string, body: Partial<CompanyInput>) =>
  client.patch<PortfolioCompany>(`/portfolio/companies/${id}`, body).then((r) => r.data);

export const deleteCompany = (id: string) =>
  client.delete(`/portfolio/companies/${id}`).then((r) => r.data);

export const addOutcome = (companyId: string, body: OutcomeInput) =>
  client
    .post<PortfolioOutcome>(`/portfolio/companies/${companyId}/outcomes`, body)
    .then((r) => r.data);

export const addSignal = (companyId: string, body: SignalInput) =>
  client
    .post<PortfolioSignal>(`/portfolio/companies/${companyId}/signals`, body)
    .then((r) => r.data);

export const deleteSignal = (companyId: string, signalId: string) =>
  client
    .delete(`/portfolio/companies/${companyId}/signals/${signalId}`)
    .then((r) => r.data);

export interface PortfolioStats {
  totals: {
    total: number;
    by_decision: { funded: number; passed: number; not_seen: number };
    by_status: Record<string, number>;
  };
  top_signals: { signal_type: string; direction: Direction; count: number }[];
}

export const fetchStats = () =>
  client.get<PortfolioStats>("/portfolio/stats").then((r) => r.data);
