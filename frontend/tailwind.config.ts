import type { Config } from "tailwindcss";

/**
 * Raed VC brand theme (light) — mirrors dashboard.apps.raed.vc.
 *
 * Colors are plain hex (not CSS vars) so Tailwind v3 alpha modifiers like
 * `bg-success/10` / `border-success/30` work out of the box. We ship a single
 * light theme (no dark toggle), so no `:root`/`.dark` indirection is needed.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#F8FAFC", // near-white page
        foreground: "#0F172A", // deep navy text
        card: "#FFFFFF",
        "card-foreground": "#0F172A",
        primary: "#1F2533", // dark navy blue
        "primary-foreground": "#FFFFFF",
        secondary: "#475569", // steel gray
        "secondary-foreground": "#FFFFFF",
        muted: "#E5E7EB", // soft gray surfaces
        "muted-foreground": "#64748B", // slate-500 — secondary text
        accent: "#EEF2F6",
        border: "#E2E8F0",
        input: "#E2E8F0",
        ring: "#1F2533",
        success: "#10B981",
        warning: "#D97706", // amber-600 — readable on light bg
        error: "#EF4444",
        info: "#2563EB", // blue-600 — links/info on light bg
        // legacy alias kept so any stray `brand` reference still resolves to navy
        brand: "#1F2533",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        heading: ["Kufam", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      borderRadius: {
        DEFAULT: "0.5rem",
        lg: "0.75rem",
        xl: "1rem",
      },
    },
  },
  plugins: [],
} satisfies Config;
