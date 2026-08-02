"use client";

import {
  AlertTriangle,
  Bot,
  Info,
  Newspaper,
  RefreshCw,
  TrendingUp,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { GaugeRing } from "../../components/GaugeRing";
import { PriceVolumeChart, hasOhlcData, type Bar } from "../../components/PriceVolumeChart";
import { delay, fetchJson } from "../../lib/api";

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
  bars?: Bar[];
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
  /** "building" = เซิร์ฟเวอร์กำลังคำนวณรอบแรกอยู่ ให้ถามซ้ำเรื่อย ๆ */
  status?: string;
};

// คำนวณรอบแรกใช้เวลาราวนาทีครึ่ง เผื่อไว้ถึง 3 นาที
const POLL_INTERVAL_MS = 5_000;
const POLL_MAX_ATTEMPTS = 36;

const VERDICT_TONE: Record<string, string> = {
  "น่าสนใจมาก": "bg-gain/10 text-gain ring-gain/30",
  "น่าสนใจ": "bg-accent-cyan/10 text-accent-cyan ring-accent-cyan/30",
  "เฝ้าดู": "bg-amber/10 text-amber ring-amber/30",
  "ยังไม่น่าเข้า": "bg-bg-raised text-ink-muted ring-bg-line"
};

function formatTime(iso: string): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "-"
    : date.toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
}

function verdictClass(verdict: string): string {
  return VERDICT_TONE[verdict] ?? VERDICT_TONE["เฝ้าดู"];
}

/** ตัวเลขหนึ่งช่องในแถบสถิติใต้กราฟ */
function Metric({
  label,
  value,
  tone
}: {
  label: string;
  value: string;
  tone?: "gain" | "loss" | "plain";
}) {
  const color =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-ink";
  return (
    <div className="rounded-xl border border-bg-line bg-bg-base/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-ink-subtle">{label}</div>
      <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${color}`}>
        {value}
      </div>
    </div>
  );
}

export default function TodayPage() {
  const [data, setData] = useState<PicksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [building, setBuilding] = useState(false);
  const [selected, setSelected] = useState<string>("");
  const [chartMode, setChartMode] = useState<"area" | "candle">("area");

  /**
   * เซิร์ฟเวอร์ตอบทันทีเสมอ ถ้ายังคำนวณไม่เสร็จจะได้ status="building"
   * จึงต้องถามซ้ำเป็นระยะ แทนการค้างรอคำขอเดียวนานเป็นนาที (ซึ่งเคยทำให้หน้าว่าง
   * จนกว่าจะกดรีเฟรชเอง)
   */
  async function load(refresh = false) {
    setError("");
    setLoading(true);
    try {
      for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
        const query = attempt === 0 && refresh ? "&refresh=true" : "";
        const payload = await fetchJson<PicksResponse>(`/picks/today?limit=10${query}`);

        if (payload.picks.length > 0) {
          setData(payload);
          setSelected((current) => current || payload.picks[0]?.symbol || "");
          return;
        }
        if (payload.status !== "building") {
          setData(payload);
          return;
        }
        setBuilding(true);
        await delay(POLL_INTERVAL_MS);
      }
      setError("คำนวณนานผิดปกติ ลองกดรีเฟรชอีกครั้ง");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดไม่สำเร็จ");
    } finally {
      setBuilding(false);
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const picks = data?.picks ?? [];
  const active = useMemo(
    () => picks.find((pick) => pick.symbol === selected) ?? picks[0],
    [picks, selected]
  );
  const aiPowered = data?.analysis_source === "gemini";
  const activeRank = active ? picks.findIndex((p) => p.symbol === active.symbol) + 1 : 0;

  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      {/* ── หัวเรื่อง ── */}
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-subtle">
            AI Stock Radar
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            หุ้นน่าสนใจวันนี้
          </h1>
          <p className="mt-1.5 max-w-xl text-sm leading-6 text-ink-muted">
            คัดจากโครงสร้างราคาและวอลุ่มผิดปกติ แล้วให้ AI อ่านกราฟกับข่าวจัดอันดับโอกาสวิ่งแรงระยะสั้น
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] font-medium ring-1 ${
              aiPowered
                ? "bg-gain/10 text-gain ring-gain/30"
                : "bg-bg-raised text-ink-muted ring-bg-line"
            }`}
          >
            <Bot className="h-3.5 w-3.5" />
            {aiPowered ? "จัดอันดับโดย Gemini" : "จัดอันดับด้วยกฎเทคนิค"}
          </span>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={loading}
            className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 rounded-full border border-bg-line bg-bg-surface px-4 text-sm font-medium text-ink-muted transition-colors duration-200 hover:text-ink disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            รีเฟรช
          </button>
        </div>
      </header>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-2xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}

      {loading && picks.length === 0 ? (
        <div className="glass px-6 py-16 text-center">
          <div className="mx-auto mb-3 h-6 w-6 animate-spin rounded-full border-2 border-bg-line border-t-gain" />
          <p className="text-sm text-ink-muted">
            {building
              ? "กำลังสแกนตลาดและให้ AI อ่านกราฟ... รอบแรกใช้เวลาราว 1-2 นาที"
              : "กำลังโหลดข้อมูล..."}
          </p>
          <p className="mt-1 text-xs text-ink-subtle">หน้าจะขึ้นเองเมื่อเสร็จ ไม่ต้องกดรีเฟรช</p>
        </div>
      ) : picks.length === 0 ? (
        !error ? (
          <div className="glass px-6 py-16 text-center text-sm text-ink-muted">
            ยังไม่พบหุ้นที่เข้าเกณฑ์
          </div>
        ) : null
      ) : (
        <>
          {/* ── สปอตไลต์: หุ้นที่เลือกอยู่ ── */}
          {/* layer-stack ต้องอยู่คนละ element กับ glass — backdrop-filter สร้าง
              stacking context ของตัวเอง ชั้นรองที่ z-index -1 จะหนีออกไปข้างหลังไม่ได้ */}
          {active ? (
            <div className="layer-stack mb-8">
            <section className="glass p-5 sm:p-6 lg:p-7">
              <div className="grid gap-6 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
                {/* ซ้าย: อันดับ + วงแหวนโอกาส + เหตุผล */}
                <div className="flex flex-col gap-4">
                  <div className="flex items-start gap-3">
                    <span className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-bg-raised text-sm font-semibold text-ink-muted">
                      {activeRank}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-3xl font-semibold leading-none tracking-tight text-ink">
                          {active.symbol}
                        </span>
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${verdictClass(
                            active.verdict
                          )}`}
                        >
                          {active.verdict}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-ink-subtle">
                        {active.name}
                        {active.sector ? ` · ${active.sector}` : ""}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-5">
                    <GaugeRing value={active.pop_probability} size={140} label="โอกาสวิ่งแรง" />
                    <div className="min-w-0 space-y-1">
                      <div className="font-mono text-2xl font-semibold tabular-nums text-ink">
                        ${active.price.toFixed(2)}
                      </div>
                      <div
                        className={`font-mono text-sm font-medium tabular-nums ${
                          active.change_percent >= 0 ? "text-gain" : "text-loss"
                        }`}
                      >
                        {active.change_percent >= 0 ? "+" : ""}
                        {active.change_percent.toFixed(2)}%
                      </div>
                      <p className="text-[11px] leading-5 text-ink-subtle">
                        ประเมินกรอบ 1-5 วันทำการ
                        <br />
                        จากราคาปิดรายวัน
                      </p>
                    </div>
                  </div>

                  <p className="text-sm leading-6 text-ink-muted">{active.reason}</p>

                  <div className="space-y-2">
                    <div className="flex items-start gap-2 rounded-xl border border-bg-line bg-bg-base/40 px-3 py-2">
                      <TrendingUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gain" />
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-ink-subtle">
                          ตัวกระตุ้น
                        </div>
                        <p className="text-xs leading-5 text-ink-muted">{active.catalyst}</p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2 rounded-xl border border-loss/20 bg-loss/[0.06] px-3 py-2">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-loss" />
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-ink-subtle">
                          ความเสี่ยง
                        </div>
                        <p className="text-xs leading-5 text-ink-muted">{active.risk}</p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* ขวา: กราฟราคา + วอลุ่ม แล้วต่อด้วยตัวเลขสำคัญ */}
                <div className="flex flex-col gap-4">
                  <div className="rounded-2xl border border-bg-line bg-bg-base/40 p-4">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <h2 className="text-xs font-medium text-ink-muted">
                          ราคาปิด 60 วันทำการล่าสุด
                        </h2>
                        <span className="text-[10px] text-ink-subtle">
                          แท่งด้านล่างคือวอลุ่มวันเดียวกัน
                        </span>
                      </div>
                      <div
                        role="group"
                        aria-label="รูปแบบกราฟ"
                        className="inline-flex overflow-hidden rounded-lg border border-bg-line"
                      >
                        {(["area", "candle"] as const).map((option) => {
                          const disabled =
                            option === "candle" && !hasOhlcData(active.bars ?? []);
                          const selectedMode = chartMode === option;
                          return (
                            <button
                              key={option}
                              type="button"
                              onClick={() => setChartMode(option)}
                              disabled={disabled}
                              aria-pressed={selectedMode}
                              title={
                                disabled ? "แหล่งข้อมูลนี้ไม่มีราคาเปิด/สูง/ต่ำ" : undefined
                              }
                              className={`cursor-pointer px-3 py-1.5 text-[11px] font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                                selectedMode
                                  ? "bg-bg-raised text-ink"
                                  : "text-ink-subtle hover:text-ink"
                              }`}
                            >
                              {option === "area" ? "พื้นที่" : "แท่งเทียน"}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <PriceVolumeChart bars={active.bars ?? []} height={200} mode={chartMode} />
                  </div>

                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Metric
                      label="วอลุ่มเทียบเฉลี่ย"
                      value={`${active.relative_volume.toFixed(2)}x`}
                      tone={active.relative_volume >= 1.5 ? "gain" : "plain"}
                    />
                    <Metric label="RSI (14)" value={active.rsi14?.toFixed(0) ?? "-"} />
                    <Metric
                      label="ต่อแนวต้านเดิม"
                      value={`${active.breakout_gap_percent >= 0 ? "+" : ""}${active.breakout_gap_percent.toFixed(1)}%`}
                      tone={active.breakout_gap_percent >= 0 ? "gain" : "plain"}
                    />
                    <Metric
                      label="ผลตอบแทน 3 เดือน"
                      value={`${active.return_3m_percent >= 0 ? "+" : ""}${active.return_3m_percent.toFixed(1)}%`}
                      tone={active.return_3m_percent >= 0 ? "gain" : "loss"}
                    />
                  </div>

                  {active.news.length > 0 ? (
                    <div className="rounded-2xl border border-bg-line bg-bg-base/40 p-4">
                      <div className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-ink-subtle">
                        <Newspaper className="h-3 w-3" />
                        ข่าวที่ใช้ประกอบการประเมิน
                      </div>
                      <ul className="space-y-1.5">
                        {active.news.slice(0, 3).map((item) => (
                          <li key={item.title} className="text-xs leading-5 text-ink-muted">
                            {item.url ? (
                              <a
                                href={item.url}
                                target="_blank"
                                rel="noreferrer"
                                className="cursor-pointer transition-colors duration-200 hover:text-accent-cyan hover:underline"
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
                </div>
              </div>
            </section>
            </div>
          ) : null}

          {/* ── แถวอันดับ: กดเพื่อสลับสปอตไลต์ ── */}
          <section>
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-sm font-semibold text-ink">อันดับทั้งหมด</h2>
              <p className="text-[11px] text-ink-subtle">
                คัดจาก {data?.universe_count ?? 0} ตัว · อัปเดต {formatTime(data?.generated_at ?? "")}
                {data?.stale ? " · กำลังคำนวณรอบใหม่" : ""}
              </p>
            </div>

            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {picks.map((pick, index) => {
                const isActive = active?.symbol === pick.symbol;
                return (
                  <li key={pick.symbol}>
                    <button
                      type="button"
                      onClick={() => setSelected(pick.symbol)}
                      aria-pressed={isActive}
                      className={`focus-ring glass flex w-full cursor-pointer items-center gap-3 p-3 text-left transition-shadow duration-200 hover:shadow-lift ${
                        isActive ? "ring-2 ring-gain/40" : ""
                      }`}
                    >
                      <span className="w-5 shrink-0 text-center text-xs font-semibold text-ink-subtle">
                        {index + 1}
                      </span>
                      <GaugeRing
                        value={pick.pop_probability}
                        size={54}
                        strokeWidth={5}
                        showValue={false}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <span className="font-mono text-sm font-semibold text-ink">
                            {pick.symbol}
                          </span>
                          <span className="font-mono text-sm font-semibold tabular-nums text-ink">
                            {Math.round(pick.pop_probability)}%
                          </span>
                        </span>
                        <span className="mt-0.5 flex items-center justify-between gap-2">
                          <span className="truncate text-[11px] text-ink-subtle">
                            {pick.verdict}
                          </span>
                          <span
                            className={`font-mono text-[11px] tabular-nums ${
                              pick.change_percent >= 0 ? "text-gain" : "text-loss"
                            }`}
                          >
                            {pick.change_percent >= 0 ? "+" : ""}
                            {pick.change_percent.toFixed(1)}%
                          </span>
                        </span>
                        <span className="mt-1 flex items-center gap-1 text-[10px] text-ink-subtle">
                          <Zap className="h-3 w-3" />
                          วอลุ่ม {pick.relative_volume.toFixed(1)}x
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <div className="mt-6 flex items-start gap-2.5 rounded-2xl border border-bg-line bg-bg-surface/60 px-4 py-3 text-xs leading-6 text-ink-muted">
            <Info className="mt-1 h-3.5 w-3.5 shrink-0 text-ink-subtle" />
            <p>
              {data?.disclaimer ??
                "ประเมินจากราคาปิดรายวัน (EOD) + ข่าวล่าสุด เป็นโซนอ้างอิงเชิงสถิติ ไม่ใช่การทำนายราคาระหว่างวัน และไม่ใช่คำแนะนำการลงทุน"}
            </p>
          </div>
        </>
      )}
    </main>
  );
}
