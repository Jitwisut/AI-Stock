"use client";

import { BrainCircuit, Flame, LineChart, ListOrdered, Radar } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "แดชบอร์ด", icon: Radar },
  { href: "/analyze", label: "วิเคราะห์หุ้น", icon: BrainCircuit },
  { href: "/surge", label: "หุ้นซิ่ง", icon: Flame },
  { href: "/longterm", label: "ระยะยาว", icon: LineChart },
  { href: "/stocks", label: "หุ้นทั้งหมด", icon: ListOrdered }
] as const;

export function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-50 border-b border-bg-line bg-bg-base/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] items-center gap-1 px-4 py-2 sm:px-6 lg:px-8">
        <span className="mr-3 hidden text-xs font-semibold uppercase tracking-wider text-ink-subtle sm:inline">
          AI Radar
        </span>
        {LINKS.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          const Icon = link.icon;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                active
                  ? "bg-gain/10 text-gain ring-1 ring-gain/30"
                  : "text-ink-muted hover:bg-bg-raised hover:text-ink"
              }`}
            >
              <Icon className="h-4 w-4" />
              {link.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
