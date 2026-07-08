import client from "./client";
import type { Lead, PaginatedLeads } from "../types/lead";

export const fetchLeads = (params?: Record<string, unknown>) =>
  client.get<PaginatedLeads>("/leads", { params }).then((r) => r.data);

/** Pull the current user's Copper-assigned leads on demand (queues a sync). */
export const syncMyLeads = () =>
  client.post<{ status: string }>("/leads/sync").then((r) => r.data);

export const fetchLead = (id: string) =>
  client.get<Lead>(`/leads/${id}`).then((r) => r.data);

export const updateLead = (id: string, data: Record<string, unknown>) =>
  client.patch<Lead>(`/leads/${id}`, data).then((r) => r.data);

export const archiveLead = (id: string) =>
  client.delete(`/leads/${id}`).then((r) => r.data);

export const archiveNoReply = (id: string) =>
  client.post(`/leads/${id}/archive-no-reply`).then((r) => r.data);

export interface LeadEvent {
  id: string;
  event_type: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export const fetchLeadEvents = (id: string) =>
  client.get<LeadEvent[]>(`/leads/${id}/events`).then((r) => r.data);

export interface ArchiveItem {
  id: string;
  company_name: string;
  website: string | null;
  company_linkedin_url: string | null;
  bucket: string | null;
  confidence_score: number | null;
  copper_opportunity_id: string | null;
  archived_at: string;
}

export type ArchiveOutcomes =
  | "sent_meeting_request"
  | "sent_rejection"
  | "sent_other"
  | "no_reply"
  | "manual";

export const fetchArchive = () =>
  client
    .get<Record<ArchiveOutcomes, ArchiveItem[]>>("/leads/archive/list")
    .then((r) => r.data);

export const exportLeadsCsv = (bucket: string) =>
  client
    .get(`/leads/export`, { params: { bucket }, responseType: "blob" })
    .then((r) => r.data as Blob);

export const findLinkedin = (id: string) =>
  client
    .post<{ company_linkedin_url: string | null; source: string | null }>(
      `/leads/${id}/find-linkedin`,
    )
    .then((r) => r.data);
