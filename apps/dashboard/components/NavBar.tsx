"use client";

import { BrainCircuit, Flame, LineChart, ListOrdered, Radar, ScanSearch, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "./ThemeToggle";

const LINKS = [
  { href: "/", label: "แดชบอร์ด", short: "หลัก", icon: Radar },
  { href: "/discover", label: "AI Discovery", short: "ค้นหา", icon: ScanSearch },
  { href: "/today", label: "น่าสนใจวันนี้", short: "วันนี้", icon: Sparkles },
  { href: "/analyze", label: "วิเคราะห์หุ้น", short: "วิเคราะห์", icon: BrainCircuit },
  { href: "/surge", label: "หุ้นซิ่ง", short: "ซิ่ง", icon: Flame },
  { href: "/longterm", label: "ระยะยาว", short: "ยาว", icon: LineChart },
  { href: "/stocks", label: "หุ้นทั้งหมด", short: "ทั้งหมด", icon: ListOrdered }
] as const;

export function NavBar() {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <>
      {/* ── Desktop top nav ── */}
      <nav className="sticky top-0 z-50 border-b border-bg-line bg-bg-surface">
        <div className="mx-auto flex max-w-[1440px] items-center gap-2 px-4 py-2.5 sm:px-6 lg:px-8">
          <span className="mr-auto text-xs font-semibold uppercase tracking-wider text-ink-subtle sm:mr-3">
            AI Radar
          </span>
          <div className="hidden items-center gap-1 sm:flex">
            {LINKS.map((link) => {
              const active = isActive(link.href);
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-200 ${
                    active
                      ? "bg-gain/10 text-gain"
                      : "text-ink-muted hover:bg-bg-raised hover:text-ink"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {link.label}
                </Link>
              );
            })}
          </div>
          <div className="ml-auto sm:ml-2">
            <ThemeToggle />
          </div>
        </div>
      </nav>

      {/* ── Mobile bottom tab bar ── */}
      <nav className="fixed bottom-0 left-0 right-0 z-40 border-t border-bg-line bg-bg-surface sm:hidden">
        <div className="flex">
          {LINKS.map((link) => {
            const active = isActive(link.href);
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`flex flex-1 cursor-pointer flex-col items-center gap-0.5 whitespace-nowrap px-0.5 py-2.5 text-[10px] font-medium transition-colors duration-200 max-[380px]:text-[9px] ${
                  active ? "text-gain" : "text-ink-subtle"
                }`}
              >
                <Icon className="h-5 w-5" />
                {link.short}
              </Link>
            );
          })}
        </div>
      </nav>
    </>
  );
}
