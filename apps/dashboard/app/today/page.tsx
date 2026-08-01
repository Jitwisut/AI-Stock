"use client";

import {
  AlertTriangle,
  Bot,
  Flame,
  Info,
  Newspaper,
  RefreshCw,
  Sparkles,
  TrendingUp
} from "lucide-react";
import { useEffect, useState } from "react";

import { Sparkline } from "../../components/Sparkline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type PickNews = {
  title: string;
  url?: string | null;
  source?: string | null;
};

type DailyPick = {
  symbol: string;
  name: string;
  sector?: string | null;
  price: number;
  change_percent: number;
  relative_volume: number;
  pop_probability: number;
  rule_score: number;
  verdict: string;
  reason: string;
  catalyst: string;
  risk: string;
  source: string;
  rsi14: number | null;
  above_sma50: boolean;
  above_sma200: boolean;
  position_in_52w_range_percent: number;
  breakout_gap_percent: number;
  return_3m_percent: number;
  volatility_percent: number;
  closes_20d: number[];
  news: PickNews[];
};

type PicksResponse = {
  generated_at: string;
  analysis_source: string;
  picks: DailyPick[];
  universe_count: number;
  disclaimer: string;
  cached?: boolean;
  stale?: boolean;
};

const VERDICT_TONE: Record<string, { text: string; ring: string; bg: string }> = {
  "น่าสนใจมาก": { text: "text-gain", ring: "ring-gain/40", bg: "bg-gain/10" },
  "น่าสนใจ": { text: "text-accent-cyan", ring: "ring-accent-cyan/40", bg: "bg-accent-cyan/10" },
  "เฝ้าดู": { text: "text-amber", ring: "ring-amber/40", bg: "bg-amber/10" },
  "ยังไม่น่าเข้า": { text: "text-ink-muted", ring: "ring-bg-line", bg: "bg-bg-raised" }
};

function probabilityColor(value: number): string {
  if (value >= 75) return "bg-gain";
  if (value >= 55) return "bg-accent-cyan";
  if (value >= 35) return "bg-amber";
  return "bg-ink-subtle";
}

function formatTime(iso: string): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "-"
    : date.toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
}

export default function TodayPage() {
  const [data, setData] = useState<PicksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load(refresh = false) {
    setError("");
    setLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/picks/today?limit=10${refresh ? "&refresh=true" : ""}`,
        { cache: "no-store" }
      );
      if (!response.ok) {
        throw new Error(
          response.status === 503
            ? "ข้อมูลราคาถูกจำกัดชั่วคราว ลองใหม่อีกครั้งใน 1-2 นาที"
            : "เรียกข้อมูลหุ้นน่าสนใจวันนี้ไม่สำเร็จ"
        );
      }
      setData((await response.json()) as PicksResponse);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const picks = data?.picks ?? [];
  const aiPowered = data?.analysis_source === "gemini";

  return (
    <main className="relative z-10 mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
      <header className="panel mb-5 flex flex-col gap-3 px-4 py-4 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber/10 ring-1 ring-amber/25">
            <Sparkles className="h-5 w-5 text-amber" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-ink sm:text-xl">หุ้นน่าสนใจวันนี้</h1>
            <p className="text-xs text-ink-muted">
              คัดจากโครงสร้างราคา + วอลุ่มผิดปกติ แล้วให้ AI อ่านกราฟและข่าวจัดอันดับโอกาสวิ่งแรงระยะสั้น
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-medium ring-1 ${
              aiPowered
                ? "bg-gain/10 text-gain ring-gain/30"
                : "bg-bg-raised text-ink-muted ring-bg-line"
            }`}
          >
            <Bot className="h-3.5 w-3.5" />
            {aiPowered ? "จัดอันดับโดย Gemini" : "จัดอันดับด้วยกฎ (AI ไม่พร้อมใช้)"}
          </span>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={loading}
            className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-bg-line bg-bg-raised px-4 text-sm font-medium text-ink-muted transition hover:text-ink disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            รีเฟรช
          </button>
        </div>
      </header>

      <div className="mb-5 flex items-start gap-3 rounded-xl border border-accent-cyan/30 bg-accent-cyan/10 px-4 py-3 text-sm text-accent-cyan">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p className="leading-6">
          {data?.disclaimer ??
            "ประเมินจากราคาปิดรายวัน (EOD) + ข่าวล่าสุด เป็นโซนอ้างอิงเชิงสถิติ ไม่ใช่การทำนายราคาระหว่างวัน และไม่ใช่คำแนะนำการลงทุน"}
        </p>
      </div>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      {data ? (
        <p className="mb-4 text-xs text-ink-subtle">
          คัดจาก {data.universe_count} ตัว · อัปเดต {formatTime(data.generated_at)}
          {data.stale ? " · กำลังคำนวณรอบใหม่เบื้องหลัง" : ""}
        </p>
      ) : null}

      {loading && picks.length === 0 ? (
        <div className="panel px-4 py-12 text-center text-sm text-ink-subtle">
          กำลังสแกนตลาดและวิเคราะห์กราฟ... รอบแรกใช้เวลาราว 1 นาที
        </div>
      ) : picks.length === 0 && !error ? (
        <div className="panel px-4 py-12 text-center text-sm text-ink-subtle">
          ยังไม่พบหุ้นที่เข้าเกณฑ์
        </div>
      ) : (
        <section className="grid gap-4 lg:grid-cols-2">
          {picks.map((pick, index) => {
            const tone = VERDICT_TONE[pick.verdict] ?? VERDICT_TONE["เฝ้าดู"];
            const up = pick.change_percent >= 0;
            return (
              <article key={pick.symbol} className="panel flex flex-col gap-3 px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 flex h-7 w-7 items-center justify-center rounded-lg bg-bg-raised text-xs font-semibold text-ink-subtle">
                      {index + 1}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-base font-semibold text-ink">
                          {pick.symbol}
                        </span>
                        <span
                          className={`inline-flex items-center rounded-lg px-2 py-0.5 text-[11px] font-bold ring-1 ${tone.ring} ${tone.text} ${tone.bg}`}
                        >
                          {pick.verdict}
                        </span>
                      </div>
                      <div className="max-w-[220px] truncate text-[11px] text-ink-subtle">
                        {pick.name}
                        {pick.sector ? ` · ${pick.sector}` : ""}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-mono text-sm font-semibold text-ink">
                      ${pick.price.toFixed(2)}
                    </div>
                    <div
                      className={`font-mono text-xs font-medium ${up ? "text-gain" : "text-loss"}`}
                    >
                      {up ? "+" : ""}
                      {pick.change_percent.toFixed(2)}%
                    </div>
                  </div>
                </div>

                {/* โอกาสวิ่งแรง */}
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px] text-ink-subtle">
                    <span className="inline-flex items-center gap-1">
                      <Flame className="h-3.5 w-3.5" />
                      โอกาสวิ่งแรงระยะสั้น (1-5 วันทำการ)
                    </span>
                    <span className="font-mono font-semibold text-ink">
                      {Math.round(pick.pop_probability)}%
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-bg-raised">
                    <div
                      className={`h-full rounded-full transition-all ${probabilityColor(
                        pick.pop_probability
                      )}`}
                      style={{ width: `${Math.max(2, Math.min(100, pick.pop_probability))}%` }}
                    />
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <Sparkline
                    points={pick.closes_20d}
                    width={120}
                    height={36}
                    className="shrink-0"
                  />
                  <dl className="grid flex-1 grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                    <div className="flex justify-between">
                      <dt className="text-ink-subtle">วอลุ่ม</dt>
                      <dd className="font-mono text-ink">{pick.relative_volume.toFixed(2)}x</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-ink-subtle">RSI</dt>
                      <dd className="font-mono text-ink">{pick.rsi14?.toFixed(0) ?? "-"}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-ink-subtle">ต่อแนวต้าน</dt>
                      <dd
                        className={`font-mono ${
                          pick.breakout_gap_percent >= 0 ? "text-gain" : "text-ink"
                        }`}
                      >
                        {pick.breakout_gap_percent >= 0 ? "+" : ""}
                        {pick.breakout_gap_percent.toFixed(1)}%
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-ink-subtle">MA200</dt>
                      <dd className={pick.above_sma200 ? "text-gain" : "text-loss"}>
                        {pick.above_sma200 ? "เหนือ" : "ใต้"}
                      </dd>
                    </div>
                  </dl>
                </div>

                <p className="text-sm leading-6 text-ink-muted">{pick.reason}</p>

                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-lg border border-bg-line bg-bg-base/40 px-3 py-2">
                    <div className="mb-0.5 flex items-center gap-1 text-[10px] uppercase tracking-wider text-ink-subtle">
                      <TrendingUp className="h-3 w-3" />
                      ตัวกระตุ้น
                    </div>
                    <p className="text-xs leading-5 text-ink-muted">{pick.catalyst}</p>
                  </div>
                  <div className="rounded-lg border border-loss/20 bg-loss/[0.06] px-3 py-2">
                    <div className="mb-0.5 flex items-center gap-1 text-[10px] uppercase tracking-wider text-loss/80">
                      <AlertTriangle className="h-3 w-3" />
                      ความเสี่ยง
                    </div>
                    <p className="text-xs leading-5 text-ink-muted">{pick.risk}</p>
                  </div>
                </div>

                {pick.news.length > 0 ? (
                  <div className="border-t border-bg-line pt-2">
                    <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-ink-subtle">
                      <Newspaper className="h-3 w-3" />
                      ข่าวที่ใช้ประกอบ
                    </div>
                    <ul className="space-y-1">
                      {pick.news.slice(0, 3).map((item) => (
                        <li key={item.title} className="text-xs leading-5 text-ink-muted">
                          {item.url ? (
                            <a
                              href={item.url}
                              target="_blank"
                              rel="noreferrer"
                              className="hover:text-accent-cyan hover:underline"
                            >
                              {item.title}
                            </a>
                          ) : (
                            item.title
                          )}
                          {item.source ? (
                            <span className="text-ink-subtle"> ({item.source})</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
