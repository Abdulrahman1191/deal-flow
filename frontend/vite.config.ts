import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev: vite runs the frontend on :5173 and proxies /api/* to the
// FastAPI backend on :3000. In production, the backend serves the built
// frontend directly — Vite is not in the picture.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": "http://localhost:3000",
      "/health": "http://localhost:3000",
    },
  },
});
