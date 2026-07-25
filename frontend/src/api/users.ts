import client from "./client";

export const fetchTeam = () =>
  client.get<string[]>("/users/team").then((r) => r.data);
