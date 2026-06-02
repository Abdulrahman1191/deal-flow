import client from "./client";

export interface FeedbackItem {
  id: string;
  user_email: string;
  page_url: string | null;
  category: string | null;
  message: string;
  resolved_at: string | null;
  created_at: string;
}

export interface FeedbackSubmit {
  message: string;
  page_url?: string | null;
  category?: string | null;
}

export const submitFeedback = (data: FeedbackSubmit) =>
  client.post<FeedbackItem>("/feedback", data).then((r) => r.data);

export const fetchFeedback = () =>
  client.get<FeedbackItem[]>("/feedback").then((r) => r.data);

export const toggleFeedbackResolved = (id: string) =>
  client.post<FeedbackItem>(`/feedback/${id}/resolve`).then((r) => r.data);
