import type { Config } from "tailwindcss";

/**
 * สีทุกตัวชี้ไปที่ CSS variable (เก็บเป็น RGB channels) เพื่อให้สลับธีมมืด/สว่าง
 * ได้จากที่เดียวใน globals.css โดยหน้าเพจไม่ต้องเขียน dark: ทุกจุด
 * รูปแบบ rgb(var(--x) / <alpha-value>) ทำให้ยังใช้ opacity แบบ bg-gain/10 ได้ตามปกติ
 */
const withAlpha = (variable: string) => `rgb(var(${variable}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base: withAlpha("--bg-base"),
          surface: withAlpha("--bg-surface"),
          raised: withAlpha("--bg-raised"),
          line: withAlpha("--bg-line")
        },
        ink: {
          DEFAULT: withAlpha("--ink"),
          muted: withAlpha("--ink-muted"),
          subtle: withAlpha("--ink-subtle")
        },
        gain: {
          DEFAULT: withAlpha("--gain"),
          soft: withAlpha("--gain-soft")
        },
        loss: {
          DEFAULT: withAlpha("--loss"),
          soft: withAlpha("--loss-soft")
        },
        amber: {
          DEFAULT: withAlpha("--amber"),
          soft: withAlpha("--amber-soft")
        },
        accent: {
          cyan: withAlpha("--accent-cyan"),
          violet: withAlpha("--accent-violet")
        }
      },
      // ขอบมนกลาง ๆ: การ์ด 18px, ปุ่ม 10-14px — ปรับที่เดียวแล้วมีผลทุกหน้า
      borderRadius: {
        md: "8px",
        lg: "10px",
        xl: "14px",
        "2xl": "18px",
        "3xl": "24px"
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"]
      },
      // เงานุ่มแบบ soft card — ค่าจริงอยู่ใน globals.css แยกตามธีม
      boxShadow: {
        soft: "var(--shadow-soft)",
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)"
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.6", transform: "scale(1.15)" }
        },
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" }
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" }
        }
      },
      animation: {
        pulseDot: "pulseDot 1.8s ease-in-out infinite",
        pulseSoft: "pulseSoft 1.6s ease-in-out infinite",
        slideUp: "slideUp 0.4s ease-out both"
      }
    }
  },
  plugins: []
};

export default config;
