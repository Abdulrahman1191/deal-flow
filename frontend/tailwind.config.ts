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
        // Navy-monochrome status scale (no green/red): YES is the most
        // prominent (navy), REJECT the most muted (light slate). Differentiation
        // comes from shade + label, not hue.
        success: "#1F2533", // YES  — navy (= primary)
        warning: "#475569", // MAYBE — slate-600
        error: "#64748B",   // REJECT — slate-500 (de-emphasised)
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
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "slide-down": {
          from: { opacity: "0", transform: "translateY(-8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
        "fade-in-up": "fade-in-up 0.3s ease-out both",
        "scale-in": "scale-in 0.18s ease-out",
        "slide-down": "slide-down 0.18s ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
