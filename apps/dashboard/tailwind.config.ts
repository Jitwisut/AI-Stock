import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Background scale
        bg: {
          base: "#020617", // page background (slate-950)
          surface: "#0B1220", // card background
          raised: "#111B2E", // elevated card / hover
          line: "#1E293B" // borders
        },
        // Text
        ink: {
          DEFAULT: "#F8FAFC",
          muted: "#94A3B8",
          subtle: "#64748B"
        },
        // Brand / signal
        gain: {
          DEFAULT: "#22C55E",
          soft: "#16A34A",
          glow: "rgba(34, 197, 94, 0.18)"
        },
        loss: {
          DEFAULT: "#EF4444",
          soft: "#DC2626",
          glow: "rgba(239, 68, 68, 0.18)"
        },
        amber: {
          DEFAULT: "#F59E0B",
          soft: "#D97706",
          glow: "rgba(245, 158, 11, 0.18)"
        },
        // Accent
        accent: {
          cyan: "#22D3EE",
          violet: "#A78BFA"
        }
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"]
      },
      boxShadow: {
        soft: "0 8px 24px rgba(2, 6, 23, 0.5)",
        glow: "0 0 0 1px rgba(34, 197, 94, 0.25), 0 8px 32px rgba(34, 197, 94, 0.12)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 24px rgba(2, 6, 23, 0.4)"
      },
      backgroundImage: {
        "grid-fade":
          "radial-gradient(circle at 50% 0%, rgba(34,197,94,0.08), transparent 60%)",
        "noise":
          "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.04 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")"
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.6", transform: "scale(1.15)" }
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" }
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" }
        }
      },
      animation: {
        pulseDot: "pulseDot 1.8s ease-in-out infinite",
        shimmer: "shimmer 2.4s linear infinite",
        slideUp: "slideUp 0.4s ease-out both"
      }
    }
  },
  plugins: []
};

export default config;
