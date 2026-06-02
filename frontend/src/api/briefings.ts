import client from "./client";
import type { Briefing, PaginatedBriefings } from "../types/briefing";

export const fetchTodayBriefing = () =>
  client.get<Briefing>("/briefings/today").then((r) => r.data);

export const fetchBriefings = (page = 1) =>
  client.get<PaginatedBriefings>("/briefings", { params: { page } }).then((r) => r.data);

export const triggerBriefing = () =>
  client.post("/briefings/generate").then((r) => r.data);
