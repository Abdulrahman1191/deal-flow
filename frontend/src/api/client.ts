import axios from "axios";

// Same-origin in production (FastAPI serves both the SPA and the API), so a
// relative baseURL is all we need. Local dev with `vite dev` also works
// because vite.config.ts proxies /api to the backend on :3000.
const client = axios.create({ baseURL: "/api/v1" });

// Local-dev convenience: append ?fake_email= to every request when set in
// localStorage. Mirrors the platform starter's local-dev convention.
//   localStorage.setItem('fake_email', 'you@raed.vc')
client.interceptors.request.use((config) => {
  if (typeof window !== "undefined" && import.meta.env?.DEV) {
    const fake = localStorage.getItem("fake_email");
    if (fake) {
      config.params = { ...(config.params ?? {}), fake_email: fake };
    }
  }
  return config;
});

client.interceptors.response.use(
  (r) => r,
  (err) => {
    // Auth is handled by the platform proxy now — a 401 from us means the
    // proxy didn't gate the request, which shouldn't happen in production.
    // In dev, it means there's no fake_email set. Either way, show a clear
    // error rather than redirecting to a /login page that no longer exists.
    if (err.response?.status === 401) {
      console.warn(
        "[client] 401 from API. In production this means the platform proxy " +
          "didn't authenticate you. In dev, run: " +
          "localStorage.setItem('fake_email','you@raed.vc')",
      );
    }
    return Promise.reject(err);
  },
);

export default client;
