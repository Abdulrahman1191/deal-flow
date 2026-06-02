import { useQuery } from "@tanstack/react-query";
import client from "../api/client";

/**
 * Whoami — fetches the logged-in user from the backend's /auth/me endpoint.
 *
 * The backend reads the X-Auth-Email header (set by the platform proxy) and
 * returns { email, name, is_owner }. We use is_owner to gate Portfolio +
 * Feedback tabs; we used to do this by decoding a JWT client-side, but the
 * platform's Slack-OTP login means we don't have a JWT anymore.
 */
export interface CurrentUser {
  email: string;
  name: string;
  is_owner: boolean;
}

export const fetchMe = () =>
  client.get<CurrentUser>("/auth/me").then((r) => r.data);

/** React Query hook for components to read the current user. */
export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    staleTime: 5 * 60 * 1000, // 5 min — identity doesn't change often
    retry: false,
  });
}
