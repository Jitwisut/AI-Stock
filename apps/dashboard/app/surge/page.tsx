"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Flame,
  Gauge,
  Info,
  Newspaper,
  ShieldAlert,
  Target,
  TimerReset,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DataSourceBadge, Delta } from "../../components/Atoms";
import { Sparkline } from "../../components/Sparkline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type SurgeTarget = { label: string; price: number };

type SurgePlan = {
  bias: string;
  data_basis: string;
  note: string;
  entry_zone?: number | null;
  key_high?: number | null;
  stop?: number | null;
  stop_pct?: number | null;
  targets: SurgeTarget[];
  risk_reward?: string | null;
  entry_note?: string | null;
  style: string;
};

type SurgeGrade = {
  grade: "S" | "A" | "B" | "C" | string;
  label: string;
  tone: "fire" | "strong" | "watch" | "caution" | string;
  is_up: boolean;
  explosive_volume: boolean;
  has_breakout: boolean;
  high_risk_mover: boolean;
  extreme_move: boolean;
  missing_s_conditions: string[];
  risk_note: string;
};

type NewsItem = {
  title: string;
  source?: string | null;
  published_at?: string | null;
};

type SurgeSignal = {
  symbol: string;
  score: number;
  surge_score: number;
  premarket_gap_percent: number;
  relative_volume: number;
  latest_price: number;
  data_source?: string;
  provider_updated_at?: string | null;
  catalyst: string;
  reason: string;
  risks: string[];
  news?: NewsItem[];
  surge_grade?: SurgeGrade;
  surge_plan?: SurgePlan;
  created_at?: string | null;
};

const GRADE_STYLE: Record<string, { ring: string; text: string; bg: string; glow: string }> = {
  fire: {
    ring: "ring-loss/40",
    text: "text-loss",
    bg: "bg-gradient-to-br from-loss/15 to-amber/10",
    glow: "bg-loss/20"
  },
  strong: {
    ring: "ring-gain/40",
    text: "text-gain",
    bg: "bg-gradient-to-br from-gain/12 to-bg-surface",
    glow: "bg-gain/20"
  },
  watch: {
    ring: "ring-amber/40",
    text: "text-amber",
    bg: "bg-gradient-to-br from-amber/10 to-bg-surface",
    glow: "bg-amber/20"
  },
  caution: {
    ring: "ring-bg-line",
    text: "text-ink-muted",
    bg: "bg-bg-surface",
    glow: "bg-ink/5"
  }
};

export default function SurgePage() {
  const [signals, setSignals] = useState<SurgeSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadSurge(fresh = false) {
    setError("");
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/signals/surge?limit=50&fresh=${fresh ? "true" : "false"}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error("เรียกข้อมูลหุ้นซิ่งไม่สำเร็จ");
      }
      setSignals((await response.json()) as SurgeSignal[]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดหุ้นซิ่งไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSurge();
  }, []);

  const stats = useMemo(() => {
    const total = signals.length;
    const sGrade = signals.filter((s) => s.surge_grade?.grade === "S").length;
    const breakout = signals.filter((s) => s.surge_grade?.has_breakout).length;
    const latestUpdate = signals
      .map((s) => Date.parse(s.provider_updated_at ?? s.created_at ?? ""))
      .filter((value) => Number.isFinite(value))
      .sort((a, b) => b - a)[0];
    return { total, sGrade, breakout, latestUpdate };
  }, [signals]);

  return (
    <main className="relative z-10 mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
      {/* Header */}
      <header className="panel mb-5 flex flex-col gap-3 px-4 py-4 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-loss/30 to-amber/20 ring-1 ring-loss/30">
            <Flame className="h-5 w-5 text-loss" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-ink sm:text-xl">หุ้นซิ่ง · ถือไว ออกไว</h1>
            <p className="text-xs text-ink-muted">
              สแกนกว้างทั้งตลาด US แล้วรีเฟรชตัวเต็งด้วย quote สดเพื่อจับ top movers หุ้นเล็ก/low-float
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void loadSurge(true)}
          disabled={loading}
          className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 self-start rounded-lg bg-gradient-to-r from-loss to-amber px-4 text-sm font-semibold text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 lg:self-auto"
        >
          <Zap className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "กำลังสแกน..." : "สแกนซิ่งใหม่"}
        </button>
      </header>

      {/* Daily-data disclaimer */}
      <div className="mb-5 flex items-start gap-3 rounded-xl border border-amber/30 bg-amber/10 px-4 py-3 text-sm text-amber">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p className="leading-6">
          ระดับราคาทั้งหมดเป็น <strong>โซนอ้างอิงจากข้อมูล daily</strong> ไม่ใช่ entry/exit แบบ intraday จริง
          ใช้เพื่อเฝ้าดูและวางแผน ต้องรอ confirmation จากราคา-วอลุ่มจริงก่อนตัดสินใจ
          ปุ่มสแกนใหม่จะขอ quote สดเท่าที่ provider รองรับ แต่ไม่ใช่คำรับประกันว่าหุ้นจะพุ่งแน่นอน
        </p>
      </div>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      {/* Stat tiles */}
      <section className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile icon={<Flame className="h-4 w-4" />} label="หุ้นซิ่งที่พบ" value={stats.total.toString()} tone="loss" loading={loading} />
        <StatTile icon={<Zap className="h-4 w-4" />} label="เกรด S" value={stats.sGrade.toString()} tone="amber" loading={loading} />
        <StatTile icon={<Target className="h-4 w-4" />} label="ทะลุไฮอ้างอิง" value={stats.breakout.toString()} tone="gain" loading={loading} />
        <StatTile icon={<Gauge className="h-4 w-4" />} label="อัปเดตล่าสุด" value={formatUpdateTime(stats.latestUpdate)} tone="violet" loading={loading} />
      </section>

      {/* Cards */}
      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="panel h-72 animate-pulse" />
          ))}
        </div>
      ) : signals.length === 0 ? (
        <div className="panel flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
          <Flame className="h-8 w-8 text-ink-subtle/40" />
          <div className="text-sm font-medium text-ink">ยังไม่พบหุ้นซิ่ง</div>
          <div className="text-xs text-ink-muted">กด “สแกนซิ่งใหม่” เพื่อคัดหุ้นเล็ก 50%+ และตัวแรงรองลงมา</div>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {signals.map((signal, index) => (
            <SurgeRow key={`${signal.symbol}-${signal.created_at ?? index}`} signal={signal} rank={index + 1} />
          ))}
        </div>
      )}

      <footer className="mt-6 text-center text-[11px] text-ink-subtle">
        หุ้นซิ่งเป็นการเก็งกำไรความเสี่ยงสูง · ข้อมูลนี้เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน
      </footer>
    </main>
  );
}

function SurgeRow({ signal, rank }: { signal: SurgeSignal; rank: number }) {
  const grade = signal.surge_grade;
  const plan = signal.surge_plan;
  const tone = grade?.tone ?? "caution";
  const style = GRADE_STYLE[tone] ?? GRADE_STYLE.caution;
  const isUp = signal.premarket_gap_percent >= 0;
  const trend: "up" | "down" | "flat" = isUp
    ? "up"
    : signal.premarket_gap_percent < -1.5
    ? "down"
    : "flat";
  const analyzeHref = `/analyze?query=${encodeURIComponent(signal.symbol)}`;
  const firstNews = signal.news?.[0];
  const catalystText = firstNews?.title || signal.reason || signal.catalyst;

  return (
    <Link
      href={analyzeHref}
      aria-label={`วิเคราะห์หุ้น ${signal.symbol}`}
      className={`panel group relative block overflow-hidden p-4 ring-1 transition hover:-translate-y-0.5 hover:border-accent-cyan/40 hover:shadow-glow ${style.ring} ${style.bg}`}
    >
      <div className={`pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full blur-2xl ${style.glow}`} />

      <div className="relative">
        {/* Top row */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={`flex h-12 w-12 items-center justify-center rounded-xl text-2xl font-bold ring-2 ${style.ring} ${style.text}`}>
              {grade?.grade ?? "–"}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-medium text-ink-subtle">#{rank}</span>
                <span className="font-mono text-lg font-semibold text-ink">{signal.symbol}</span>
                <span className="text-xs text-ink-muted">${signal.latest_price.toFixed(2)}</span>
                <DataSourceBadge source={signal.data_source} />
              </div>
              <div className={`text-xs font-semibold ${style.text}`}>{grade?.label ?? "—"}</div>
              <div className="mt-0.5 text-[10px] text-ink-subtle">
                อัปเดต {formatUpdateTime(Date.parse(signal.provider_updated_at ?? signal.created_at ?? ""))}
              </div>
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-ink-subtle">Surge</div>
            <div className={`stat-number text-xl font-bold ${style.text}`}>
              {Math.round(signal.surge_score)}
            </div>
            <div className="mt-1 inline-flex items-center justify-end gap-1 text-[10px] font-medium text-accent-cyan opacity-70 transition group-hover:opacity-100">
              วิเคราะห์
              <ArrowUpRight className="h-3 w-3" />
            </div>
          </div>
        </div>

        {/* Metrics row */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Delta value={signal.premarket_gap_percent} className="text-sm" />
          <span className="chip text-ink-muted">
            <Gauge className="h-3 w-3" /> Vol {signal.relative_volume.toFixed(1)}x
          </span>
          {grade?.has_breakout ? (
            <span className="chip border-gain/30 bg-gain/10 text-gain">
              <ArrowUpRight className="h-3 w-3" /> ทะลุไฮ
            </span>
          ) : null}
          {grade?.explosive_volume ? (
            <span className="chip border-amber/30 bg-amber/10 text-amber">
              <Zap className="h-3 w-3" /> วอลุ่มพุ่ง
            </span>
          ) : null}
          {grade?.high_risk_mover ? (
            <span className="chip border-loss/30 bg-loss/10 text-loss">
              <AlertTriangle className="h-3 w-3" /> High Risk Mover
            </span>
          ) : null}
          <div className="ml-auto">
            <Sparkline points={undefined} trend={trend} width={88} height={24} showDot={false} />
          </div>
        </div>

        {grade?.risk_note ? (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-loss/25 bg-loss/10 px-3 py-2 text-[11px] leading-5 text-loss">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{grade.risk_note}</span>
          </div>
        ) : null}

        {catalystText ? (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-bg-line bg-bg-base/35 px-3 py-2 text-[11px] leading-5 text-ink-muted">
            {firstNews ? (
              <Newspaper className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-cyan" />
            ) : (
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-cyan" />
            )}
            <span>
              <span className="font-medium text-ink">{firstNews ? "ข่าวล่าสุด: " : "เหตุผล: "}</span>
              {catalystText}
              {firstNews?.source ? <span className="text-ink-subtle"> ({firstNews.source})</span> : null}
            </span>
          </div>
        ) : null}

        {grade?.missing_s_conditions?.length ? (
          <div className="mt-3 rounded-xl border border-bg-line bg-bg-base/30 px-3 py-2 text-[11px] leading-5 text-ink-muted">
            <span className="font-medium text-ink">ยังไม่เป็น S เพราะ </span>
            {grade.missing_s_conditions.join(" · ")}
          </div>
        ) : null}

        {/* Trade plan */}
        {plan ? (
          <div className="mt-4 rounded-xl border border-bg-line bg-bg-base/40 p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
                <TimerReset className="h-3 w-3" /> แผนถือไว-ออกไว
              </div>
              <span className="text-[10px] text-ink-subtle">อ้างอิง {plan.data_basis}</span>
            </div>

            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <PlanLevel label="โซนเข้า" value={plan.entry_zone} tone="text-ink" />
              <PlanLevel
                label={`Stop${plan.stop_pct ? ` -${plan.stop_pct}%` : ""}`}
                value={plan.stop}
                tone="text-loss"
                icon={<ArrowDownRight className="h-3 w-3" />}
              />
              {plan.targets.slice(0, 2).map((target) => (
                <PlanLevel
                  key={target.label}
                  label={target.label}
                  value={target.price}
                  tone="text-gain"
                  icon={<ArrowUpRight className="h-3 w-3" />}
                />
              ))}
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-muted">
              <span>
                Bias: <span className="font-medium text-ink">{plan.bias}</span>
              </span>
              {plan.risk_reward ? (
                <span>
                  R:R <span className="font-mono font-medium text-ink">{plan.risk_reward}</span>
                </span>
              ) : null}
              {plan.key_high ? (
                <span>
                  ไฮอ้างอิง <span className="font-mono text-ink">${plan.key_high.toFixed(2)}</span>
                </span>
              ) : null}
            </div>

            {plan.entry_note ? (
              <p className="mt-2 text-[11px] leading-5 text-ink-muted">{plan.entry_note}</p>
            ) : null}
            <p className="mt-1 text-[11px] leading-5 text-amber/90">{plan.style}</p>
          </div>
        ) : null}

        {/* Risks */}
        {signal.risks.length > 0 ? (
          <div className="mt-3 flex items-start gap-2 text-[11px] text-ink-subtle">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-loss/70" />
            <span>{signal.risks.join(" · ")}</span>
          </div>
        ) : null}
      </div>
    </Link>
  );
}

function formatUpdateTime(timestamp?: number) {
  if (!timestamp || !Number.isFinite(timestamp)) {
    return "—";
  }
  return new Intl.DateTimeFormat("th-TH", {
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short"
  }).format(new Date(timestamp));
}

function PlanLevel({
  label,
  value,
  tone,
  icon
}: {
  label: string;
  value?: number | null;
  tone: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-bg-line bg-bg-surface/60 px-2.5 py-1.5">
      <div className="flex items-center gap-1 text-[10px] text-ink-subtle">
        {icon}
        {label}
      </div>
      <div className={`stat-number text-sm font-semibold tabular-nums ${tone}`}>
        {typeof value === "number" ? `$${value.toFixed(2)}` : "—"}
      </div>
    </div>
  );
}

function StatTile({
  icon,
  label,
  value,
  tone,
  loading
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: "loss" | "amber" | "gain" | "violet";
  loading?: boolean;
}) {
  const accent = {
    loss: "text-loss",
    amber: "text-amber",
    gain: "text-gain",
    violet: "text-accent-violet"
  }[tone];

  return (
    <div className="panel p-4">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-ink-muted">
        <span className={accent}>{icon}</span>
        {label}
      </div>
      <div className="mt-3">
        {loading ? (
          <div className="skeleton h-7 w-16 rounded" />
        ) : (
          <span className="stat-number text-2xl font-semibold text-ink">{value}</span>
        )}
      </div>
    </div>
  );
}
