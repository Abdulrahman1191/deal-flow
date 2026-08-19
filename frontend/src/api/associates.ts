import client from "./client";

export interface AssociatePerformance {
  email: string;
  leads_total: number;
  backlog: number;
  awaiting_deck: number;
  active: number;
  outreach_sent: number;
  approved: number;
  converted: number;
  archived: number;
}

export interface AssociatesPerformance {
  associates: AssociatePerformance[];
}

export const fetchAssociatesPerformance = () =>
  client.get<AssociatesPerformance>("/associates/performance").then((r) => r.data);
