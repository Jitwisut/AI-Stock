"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

type Theme = "dark" | "light";

/** อ่านธีมที่ script ใน layout ตั้งไว้แล้ว (ไม่คำนวณใหม่ ป้องกันค่าไม่ตรงกัน) */
function currentTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function ThemeToggle() {
  // เริ่มที่ dark เสมอฝั่ง server แล้วค่อย sync ตอน mount — กัน hydration mismatch
  const [theme, setTheme] = useState<Theme>("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(currentTheme());
    setMounted(true);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem("theme", next);
    } catch {
      // โหมดส่วนตัวของบางเบราว์เซอร์ปิด localStorage — ให้สลับได้ต่อในหน้านี้
    }
    setTheme(next);
  }

  const nextLabel = theme === "dark" ? "สลับเป็นธีมสว่าง" : "สลับเป็นธีมมืด";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={nextLabel}
      title={nextLabel}
      className="focus-ring inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg border border-bg-line bg-bg-raised text-ink-muted transition-colors duration-200 hover:text-ink"
    >
      {/* ก่อน mount ยังไม่รู้ธีมจริง — โชว์ไอคอนกลาง ๆ ไว้ก่อนกันภาพกระพริบ */}
      {!mounted ? (
        <Sun className="h-4 w-4 opacity-0" />
      ) : theme === "dark" ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </button>
  );
}
