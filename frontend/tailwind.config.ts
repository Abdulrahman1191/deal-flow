import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: "#6C47FF",
      },
    },
  },
  plugins: [],
} satisfies Config;
