import client from "./client";

export const fetchTeam = () =>
  client.get<string[]>("/users/team").then((r) => r.data);

export type RosterEntry = { email: string; name: string | null };

export const fetchRoster = () =>
  client.get<RosterEntry[]>("/users/roster").then((r) => r.data);
