"use client";

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bell,
  CheckCircle2,
  ChevronRight,
  Database,
  ExternalLink,
  Eye,
  Filter,
  Flame,
  Newspaper,
  Radar,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingUp,
  Zap
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { DataSourceBadge, Delta, ScoreBadge } from "../components/Atoms";
import { Sparkline } from "../components/Sparkline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const ADMIN_API_KEY = process.env.NEXT_PUBLIC_ADMIN_API_KEY ?? "";

type ScoreBreakdown = {
  momentum: number;
  volume: number;
  catalyst: number;
  premarket_gap: number;
  breakout: number;
  risk: number;
};

type NewsItem = {
  title: string;
  url: string;
  source: string;
  published_at: string;
  summary?: string | null;
  sentiment_score?: number | null;
};

type Signal = {
  id?: number | null;
  symbol: string;
  score: number;
  premarket_gap_percent: number;
  relative_volume: number;
  latest_price: number;
  data_source?: string;
  catalyst: string;
  reason: string;
  risks: string[];
  watch_plan: string[];
  score_breakdown: ScoreBreakdown;
  score_breakdown_max?: Partial<ScoreBreakdown>;
  surge_score?: number;
  recommendation?: Recommendation;
  news: NewsItem[];
  created_at?: string | null;
};

type Recommendation = {
  label: string;
  short_label: string;
  reason: string;
  level: "strong" | "watch" | "caution" | string;
};

type SettingsStatus = {
  market_data_provider: string;
  ai_provider: string;
  use_live_data: boolean;
  allow_mock_data: boolean;
  save_signals: boolean;
  send_telegram: boolean;
  telegram_configured: boolean;
  openai_configured: boolean;
  gemini_configured: boolean;
  ai_configured: boolean;
  database_reachable: boolean;
  universe_mode: string;
  scan_universe_limit: number;
  ai_candidate_limit: number;
};

type ScanResult = {
  signals: Signal[];
  summary: string;
  telegram_status: string;
  saved_signal_count: number;
  scanned_symbol_count: number;
  universe_symbol_count: number;
  failed_symbol_count: number;
  candidate_symbol_count: number;
};

type ScanRunStatus = {
  id: number;
  kind: string;
  status: string;
  provider: string;
};

type Stock = {
  symbol: string;
  name: string;
  exchange: string;
  sector?: string | null;
  is_active: boolean;
};

export default function DashboardPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [summary, setSummary] = useState("");
  const [stockCount, setStockCount] = useState(0);
  const [scanMeta, setScanMeta] = useState({
    scanned: 0,
    universe: 0,
    candidates: 0,
    failed: 0
  });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingStocks, setLoadingStocks] = useState(false);
  const [runningScan, setRunningScan] = useState(false);
  const [error, setError] = useState("");

  const selected = useMemo(() => {
    return signals.find((signal) => signal.symbol === selectedSymbol) ?? signals[0];
  }, [signals, selectedSymbol]);

  const filteredSignals = useMemo(() => {
    const normalized = query.trim().toUpperCase();
    if (!normalized) return signals;
    return signals.filter((signal) => signal.symbol.includes(normalized));
  }, [query, signals]);

  const surgeSignals = useMemo(() => {
    return [...signals]
      .sort((left, right) => getSurgeScore(right) - getSurgeScore(left))
      .slice(0, 4);
  }, [signals]);
  const scanDisabled =
    runningScan || Boolean(status && (!status.use_live_data || status.allow_mock_data));

  async function loadDashboard() {
    setError("");
    setLoading(true);
    try {
      const [signalResponse, statusResponse] = await Promise.all([
        fetch(`${API_BASE_URL}/signals/latest?limit=10`, { cache: "no-store" }),
        fetch(`${API_BASE_URL}/settings/status`, { cache: "no-store" })
      ]);
      if (!signalResponse.ok || !statusResponse.ok) {
        throw new Error("เรียกข้อมูลจาก API ไม่สำเร็จ");
      }
      const signalData = (await signalResponse.json()) as Signal[];
      const statusData = (await statusResponse.json()) as SettingsStatus;
      setSignals(signalData);
      setStatus(statusData);
      setScanMeta((current) => ({
        ...current,
        universe: statusData.scan_universe_limit || current.universe
      }));
      setSelectedSymbol((current) => current || signalData[0]?.symbol || "");
      void loadStocks();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดแดชบอร์ดไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  async function runScan() {
    setRunningScan(true);
    setError("");
    try {
      const headers: HeadersInit = ADMIN_API_KEY ? { "X-Admin-API-Key": ADMIN_API_KEY } : {};
      const response = await fetch(`${API_BASE_URL}/scan/run?kind=momentum`, { method: "POST", headers });
      if (!response.ok) {
        throw new Error("รันสแกนไม่สำเร็จ");
      }
      const payload = (await response.json()) as ScanResult | ScanRunStatus;
      if ("status" in payload && "id" in payload && !("signals" in payload)) {
        setSummary(`ส่งงานสแกนเข้าคิวแล้ว (#${payload.id}) สถานะ ${payload.status}`);
        await loadDashboard();
        return;
      }
      const result = payload as ScanResult;
      setSignals(result.signals);
      setSummary(result.summary);
      setScanMeta({
        scanned: result.scanned_symbol_count,
        universe: result.universe_symbol_count,
        candidates: result.candidate_symbol_count,
        failed: result.failed_symbol_count
      });
      setSelectedSymbol(result.signals[0]?.symbol || "");
      await loadStatus();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "รันสแกนไม่สำเร็จ");
    } finally {
      setRunningScan(false);
    }
  }

  async function loadStatus() {
    const response = await fetch(`${API_BASE_URL}/settings/status`, { cache: "no-store" });
    if (response.ok) {
      setStatus((await response.json()) as SettingsStatus);
    }
  }

  async function loadStocks() {
    setLoadingStocks(true);
    try {
      const response = await fetch(`${API_BASE_URL}/stocks`, { cache: "no-store" });
      if (response.ok) {
        const stockData = (await response.json()) as Stock[];
        setStocks(stockData);
        setStockCount(stockData.length);
      }
    } catch {
      setStocks([]);
      setStockCount(0);
    } finally {
      setLoadingStocks(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  return (
    <main className="relative z-10 mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
      {/* ─────────── TOP BAR ─────────── */}
      <header className="panel mb-5 flex flex-col gap-3 px-4 py-3 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-gain/30 to-accent-cyan/20 ring-1 ring-gain/30">
            <Radar className="h-5 w-5 text-gain" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-semibold text-ink sm:text-lg">AI Momentum Radar</h1>
              <span className="chip border-gain/30 bg-gain/10 text-gain">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-gain opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-gain" />
                </span>
                LIVE
              </span>
            </div>
            <p className="text-xs text-ink-muted">
              ตลาดสหรัฐ · Pre-market scan · สรุปเป็นภาษาไทย
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            title="รีเฟรชแดชบอร์ด"
            onClick={() => void loadDashboard()}
            className="focus-ring inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg border border-bg-line bg-bg-raised text-ink-muted transition hover:border-ink/40 hover:text-ink"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => void runScan()}
            disabled={scanDisabled}
            className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg bg-gradient-to-r from-gain to-gain-soft px-4 text-sm font-semibold text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Sparkles className={`h-4 w-4 ${runningScan ? "animate-spin" : ""}`} />
            {runningScan ? "กำลังสแกน..." : "สแกนใหม่"}
          </button>
        </div>
      </header>

      {/* Live data warning */}
      {status && (!status.use_live_data || status.allow_mock_data) ? (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-amber/30 bg-amber/10 px-4 py-3 text-sm text-amber">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="leading-6">
            ตรวจพบว่า live data ยังไม่พร้อมหรือยังเปิด mock อยู่ ตั้งค่า{" "}
            <code className="rounded bg-amber/20 px-1 font-mono text-xs">USE_LIVE_DATA=true</code>{" "}
            และ <code className="rounded bg-amber/20 px-1 font-mono text-xs">ALLOW_MOCK_DATA=false</code>{" "}
            เพื่อป้องกันราคาปลอม
          </p>
        </div>
      ) : null}

      {/* Error */}
      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      {/* ─────────── METRIC TILES ─────────── */}
      <section className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricTile
          icon={<Database className="h-4 w-4" />}
          label="Universe"
          value={formatUniverse(status, stockCount)}
          hint="จำนวนหุ้นทั้งหมดในตลาด"
          loading={loading}
        />
        <MetricTile
          icon={<Activity className="h-4 w-4" />}
          label="สแกนล่าสุด"
          value={scanMeta.scanned ? scanMeta.scanned.toLocaleString() : "—"}
          unit={scanMeta.scanned ? "ตัว" : "รอกดสแกน"}
          hint="จำนวนหุ้นที่สแกนราคา/ข่าว"
          loading={loading}
          tone="gain"
        />
        <MetricTile
          icon={<Target className="h-4 w-4" />}
          label="AI Candidates"
          value={
            scanMeta.candidates
              ? scanMeta.candidates.toLocaleString()
              : (status?.ai_candidate_limit ?? 50).toString()
          }
          unit={scanMeta.candidates ? "ตัว" : "ตัวแรก"}
          hint="หุ้นที่ผ่านเกณฑ์คัดกรอง"
          loading={loading}
          tone="amber"
        />
        <MetricTile
          icon={<Zap className="h-4 w-4" />}
          label="โหมดวิเคราะห์"
          value={formatAiProvider(status)}
          hint={status?.ai_configured ? "ใช้ LLM สรุปสัญญาณ" : "ใช้ scoring engine"}
          loading={loading}
          tone="violet"
        />
      </section>

      {/* ─────────── SYSTEM STATUS CHIPS ─────────── */}
      <section className="mb-5 flex flex-wrap items-center gap-2">
        <StatusChip
          icon={<Server className="h-3.5 w-3.5" />}
          label="ข้อมูลตลาด"
          value={status?.use_live_data ? "Live" : "Mock"}
          active={Boolean(status?.use_live_data)}
        />
        <StatusChip
          icon={<Database className="h-3.5 w-3.5" />}
          label="Database"
          value={status?.database_reachable ? "Connected" : "In-memory"}
          active={Boolean(status?.database_reachable)}
        />
        <StatusChip
          icon={<Bell className="h-3.5 w-3.5" />}
          label="Telegram"
          value={status?.telegram_configured ? "พร้อมส่ง" : "ยังไม่ตั้งค่า"}
          active={Boolean(status?.telegram_configured)}
        />
        <div className="ml-auto text-xs text-ink-subtle">
          เวลาตลาด: {new Date().toLocaleString("th-TH", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })}
        </div>
      </section>

      {/* ─────────── SURGE WATCHLIST ─────────── */}
      <section className="mb-5">
        <SectionHeader
          icon={<Flame className="h-4 w-4" />}
          kicker="Surge watchlist"
          title="ตัวที่น่าเฝ้าดูเป็นพิเศษ"
          subtitle="คัดจาก momentum, การเปลี่ยนแปลงล่าสุด, วอลุ่ม และข่าวหนุน"
        />
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {surgeSignals.length === 0 ? (
            <EmptyState
              className="sm:col-span-2 xl:col-span-4"
              title="ยังไม่มีสัญญาณ"
              description="กดปุ่ม “สแกนใหม่” เพื่อเริ่มสแกนหุ้น momentum"
            />
          ) : (
            surgeSignals.map((signal) => (
              <SurgeCard
                key={`surge-${signal.symbol}-${signal.created_at ?? "latest"}`}
                signal={signal}
                selected={selected?.symbol === signal.symbol}
                onClick={() => setSelectedSymbol(signal.symbol)}
              />
            ))
          )}
        </div>
        <p className="mt-3 text-xs leading-5 text-ink-subtle">
          * แถบนี้เป็น watchlist ช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน ควรรอ confirmation จากราคาและวอลุ่มก่อนตัดสินใจ
        </p>
      </section>

      {/* ─────────── MAIN GRID: SIGNALS + DETAIL ─────────── */}
      <section className="mb-5 grid gap-5 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        {/* Signal list */}
        <div className="panel overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-bg-line p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-gain">
                <TrendingUp className="h-3.5 w-3.5" />
                Top signals
              </div>
              <h2 className="mt-1 text-base font-semibold text-ink">สัญญาณที่น่าสนใจ</h2>
              <p className="text-xs text-ink-muted">
                {loading ? "กำลังโหลด..." : `${filteredSignals.length} ตัวที่เข้าเกณฑ์`}
              </p>
            </div>
            <label className="flex h-9 w-full min-w-0 items-center gap-2 rounded-lg border border-bg-line bg-bg-base/60 px-3 sm:w-64">
              <Search className="h-3.5 w-3.5 text-ink-subtle" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="ค้นหา symbol..."
                className="w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle outline-none"
              />
            </label>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] border-collapse text-left text-sm">
              <thead className="bg-bg-base/40 text-[11px] uppercase tracking-wider text-ink-subtle">
                <tr>
                  <th className="px-4 py-3 font-medium">Symbol</th>
                  <th className="px-4 py-3 font-medium">Score</th>
                  <th className="px-4 py-3 font-medium">Change</th>
                  <th className="px-4 py-3 font-medium">Vol</th>
                  <th className="px-4 py-3 font-medium">Trend</th>
                  <th className="px-4 py-3 font-medium">คำแนะนำ</th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-sm text-ink-subtle">
                      {loading ? "กำลังโหลด..." : "ยังไม่มีสัญญาณ — กดสแกนใหม่เพื่อเริ่ม"}
                    </td>
                  </tr>
                ) : (
                  filteredSignals.map((signal) => {
                    const recommendation = getRecommendation(signal);
                    const isSelected = selected?.symbol === signal.symbol;
                    return (
                      <tr
                        key={`${signal.symbol}-${signal.created_at ?? "latest"}`}
                        onClick={() => setSelectedSymbol(signal.symbol)}
                        className={`group cursor-pointer border-t border-bg-line transition ${
                          isSelected
                            ? "bg-gain/[0.06]"
                            : "hover:bg-bg-raised/50"
                        }`}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-semibold text-ink">
                              {signal.symbol}
                            </span>
                            <span className="text-[11px] text-ink-subtle">
                              ${signal.latest_price.toFixed(2)}
                            </span>
                            <DataSourceBadge source={signal.data_source} />
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <ScoreBadge score={signal.score} size="sm" />
                        </td>
                        <td className="px-4 py-3">
                          <Delta value={signal.premarket_gap_percent} />
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-ink-muted">
                          {signal.relative_volume.toFixed(1)}x
                        </td>
                        <td className="px-4 py-3">
                          <Sparkline
                            points={undefined}
                            trend={
                              signal.premarket_gap_percent > 1.5
                                ? "up"
                                : signal.premarket_gap_percent < -1.5
                                ? "down"
                                : "flat"
                            }
                            width={64}
                            height={22}
                            showDot={false}
                            className="opacity-80 transition group-hover:opacity-100"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <RecommendationTag
                            level={recommendation.levelTag}
                            label={recommendation.shortLabel}
                            className={recommendation.className}
                          />
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Detail */}
        <aside className="panel p-4 lg:sticky lg:top-5 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          {selected ? (
            <SignalDetail signal={selected} summary={summary} />
          ) : (
            <div className="flex min-h-[360px] flex-col items-center justify-center gap-2 text-center text-sm text-ink-subtle">
              <Eye className="h-8 w-8 text-ink-subtle/50" />
              <p>{loading ? "กำลังโหลดสัญญาณ..." : "เลือกหุ้นจากตารางเพื่อดูรายละเอียด"}</p>
            </div>
          )}
        </aside>
      </section>

      {/* ─────────── UNIVERSE + LONG-TERM LINKS ─────────── */}
      <section className="grid gap-3 sm:grid-cols-2">
        <Link
          href="/stocks"
          className="focus-ring group panel flex items-center justify-between gap-3 p-4 transition hover:border-accent-cyan/40"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-cyan/15 text-accent-cyan ring-1 ring-accent-cyan/30">
              <Filter className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-ink">ดูหุ้นทั้งหมดในตลาด</h2>
              <p className="text-xs text-ink-muted">
                {loadingStocks ? "กำลังโหลด..." : `${stockCount.toLocaleString()} ตัว · ค้นหา/กรองได้`}
              </p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-ink-subtle transition group-hover:translate-x-0.5 group-hover:text-ink" />
        </Link>

        <Link
          href="/longterm"
          className="focus-ring group panel flex items-center justify-between gap-3 p-4 transition hover:border-gain/40"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gain/15 text-gain ring-1 ring-gain/30">
              <TrendingUp className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-ink">หุ้นน่าสะสมระยะยาว</h2>
              <p className="text-xs text-ink-muted">จัดอันดับเชิงเทรนด์ (MA200, โมเมนตัม, 52 สัปดาห์)</p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-ink-subtle transition group-hover:translate-x-0.5 group-hover:text-ink" />
        </Link>
      </section>

      <footer className="mt-6 text-center text-[11px] text-ink-subtle">
        AI Momentum Radar · ข้อมูลนี้เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน
      </footer>
    </main>
  );
}

/* ──────────────────────────────────────────────────────────── */
/*                       SUB COMPONENTS                          */
/* ──────────────────────────────────────────────────────────── */

function SectionHeader({
  icon,
  kicker,
  title,
  subtitle
}: {
  icon: ReactNode;
  kicker: string;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber">
          {icon}
          {kicker}
        </div>
        <h2 className="mt-0.5 text-base font-semibold text-ink">{title}</h2>
      </div>
      {subtitle ? <p className="text-xs text-ink-muted">{subtitle}</p> : null}
    </div>
  );
}

function MetricTile({
  icon,
  label,
  value,
  unit,
  hint,
  loading,
  tone = "neutral"
}: {
  icon: ReactNode;
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  loading?: boolean;
  tone?: "neutral" | "gain" | "amber" | "violet";
}) {
  const accent = {
    neutral: "text-ink-muted",
    gain: "text-gain",
    amber: "text-amber",
    violet: "text-accent-violet"
  }[tone];

  return (
    <div className="panel p-4 transition hover:border-ink/20">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-ink-muted">
          <span className={accent}>{icon}</span>
          {label}
        </div>
      </div>
      <div className="mt-3 flex items-baseline gap-1.5">
        {loading ? (
          <div className="skeleton h-7 w-20 rounded" />
        ) : (
          <>
            <span className="stat-number text-2xl font-semibold text-ink">{value}</span>
            {unit ? <span className="text-xs text-ink-subtle">{unit}</span> : null}
          </>
        )}
      </div>
      {hint ? <div className="mt-1 text-[11px] text-ink-subtle">{hint}</div> : null}
    </div>
  );
}

function StatusChip({
  icon,
  label,
  value,
  active
}: {
  icon: ReactNode;
  label: string;
  value: string;
  active: boolean;
}) {
  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition ${
        active
          ? "border-gain/30 bg-gain/10 text-gain"
          : "border-bg-line bg-bg-surface text-ink-muted"
      }`}
    >
      <span className={active ? "text-gain" : "text-ink-subtle"}>{icon}</span>
      <span className="text-ink-subtle">{label}:</span>
      <span className="font-medium">{value}</span>
      {active ? (
        <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-gain shadow-[0_0_8px_rgba(34,197,94,0.6)]" />
      ) : null}
    </div>
  );
}

function SurgeCard({
  signal,
  selected,
  onClick
}: {
  signal: Signal;
  selected: boolean;
  onClick: () => void;
}) {
  const recommendation = getRecommendation(signal);
  const isUp = signal.premarket_gap_percent > 0;
  const trend: "up" | "down" | "flat" = isUp
    ? "up"
    : signal.premarket_gap_percent < -1.5
    ? "down"
    : "flat";

  return (
    <button
      type="button"
      onClick={onClick}
      className={`focus-ring group relative cursor-pointer overflow-hidden rounded-2xl border p-4 text-left transition ${
        selected
          ? "border-gain/50 bg-gradient-to-br from-gain/[0.08] to-bg-surface shadow-glow"
          : "border-bg-line bg-bg-surface hover:border-ink/30 hover:bg-bg-raised"
      }`}
    >
      {/* Decorative glow */}
      <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-gain/10 blur-2xl transition group-hover:bg-gain/20" />

      <div className="relative">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-lg font-semibold text-ink">{signal.symbol}</span>
              <ChevronRight className="h-3.5 w-3.5 text-ink-subtle opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
            </div>
            <div className="mt-0.5 text-[11px] text-ink-subtle">
              ${signal.latest_price.toFixed(2)}
            </div>
            <div className="mt-2">
              <DataSourceBadge source={signal.data_source} />
            </div>
          </div>
          <ScoreBadge score={signal.score} size="sm" />
        </div>

        {/* Sparkline */}
        <div className="mt-3 -mx-1">
          <Sparkline points={undefined} trend={trend} width={200} height={42} />
        </div>

        {/* Metrics */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span
            className={`chip ${
              isUp
                ? "border-gain/30 bg-gain/10 text-gain"
                : "border-loss/30 bg-loss/10 text-loss"
            }`}
          >
            {isUp ? "+" : ""}
            {signal.premarket_gap_percent.toFixed(1)}%
          </span>
          <span className="chip text-ink-muted">
            Vol {signal.relative_volume.toFixed(1)}x
          </span>
        </div>

        <div className={`mt-3 text-sm font-semibold ${recommendation.className}`}>
          {recommendation.label}
        </div>
        <p className="mt-0.5 line-clamp-2 text-[11px] leading-5 text-ink-subtle">
          {recommendation.reason}
        </p>
      </div>
    </button>
  );
}

function RecommendationTag({
  level,
  label,
  className = ""
}: {
  level: "strong" | "watch" | "caution";
  label: string;
  className?: string;
}) {
  const config = {
    strong: { bg: "bg-gain/10", text: "text-gain", border: "border-gain/30", icon: Flame },
    watch: { bg: "bg-amber/10", text: "text-amber", border: "border-amber/30", icon: Eye },
    caution: { bg: "bg-loss/10", text: "text-loss", border: "border-loss/30", icon: ShieldAlert }
  }[level];

  const Icon = config.icon;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${config.bg} ${config.text} ${config.border} ${className}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

function SignalDetail({ signal, summary }: { signal: Signal; summary: string }) {
  const breakdown = Object.entries(signal.score_breakdown);
  const recommendation = getRecommendation(signal);
  const isUp = signal.premarket_gap_percent > 0;
  const trend: "up" | "down" | "flat" = isUp
    ? "up"
    : signal.premarket_gap_percent < -1.5
    ? "down"
    : "flat";

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-ink-subtle">
            สัญญาณที่เลือก
          </div>
          <div className="mt-0.5 flex items-center gap-2">
            <h2 className="font-mono text-2xl font-semibold text-ink">{signal.symbol}</h2>
            <span className="text-sm text-ink-muted">${signal.latest_price.toFixed(2)}</span>
            <DataSourceBadge source={signal.data_source} />
          </div>
        </div>
        <ScoreBadge score={signal.score} size="md" showLabel />
      </div>

      {/* Recommendation */}
      <div className="rounded-xl border border-bg-line bg-bg-raised/60 p-3">
        <div className={`flex items-center gap-2 text-sm font-semibold ${recommendation.className}`}>
          {recommendation.levelTag === "strong" ? (
            <Flame className="h-4 w-4" />
          ) : recommendation.levelTag === "watch" ? (
            <Eye className="h-4 w-4" />
          ) : (
            <ShieldAlert className="h-4 w-4" />
          )}
          {recommendation.label}
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-muted">{recommendation.reason}</p>
      </div>

      {/* Sparkline big */}
      <div className="rounded-xl border border-bg-line bg-bg-base/40 p-3">
        <div className="flex items-center justify-between">
          <div className="text-[11px] font-medium uppercase tracking-wider text-ink-subtle">
            Price trend
          </div>
          <Delta value={signal.premarket_gap_percent} className="text-sm" />
        </div>
        <div className="mt-1">
          <Sparkline points={undefined} trend={trend} width={320} height={56} />
        </div>
      </div>

      {/* Score breakdown */}
      <section>
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
          รายละเอียดคะแนน
        </h3>
        <div className="mt-3 flex flex-col gap-2.5">
          {breakdown.map(([label, value]) => {
            const max =
              signal.score_breakdown_max?.[label as keyof ScoreBreakdown] ??
              DEFAULT_SCORE_MAX[label as keyof ScoreBreakdown] ??
              25;
            return (
              <div key={label}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="text-ink-muted">{formatScoreLabel(label)}</span>
                  <span className="font-mono font-medium text-ink">
                    {value.toFixed(1)} <span className="text-ink-subtle">/{max}</span>
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-bg-base">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-gain to-gain-soft transition-all"
                    style={{ width: `${Math.min(100, (value / max) * 100)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Catalyst */}
      <section>
        <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
          <Newspaper className="h-3 w-3" /> ปัจจัยหนุน
        </h3>
        <p className="mt-2 text-sm leading-6 text-ink">{signal.catalyst}</p>
        <p className="mt-1 text-xs leading-5 text-ink-muted">{signal.reason}</p>
      </section>

      {/* Risks */}
      <section>
        <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-loss">
          <ShieldAlert className="h-3 w-3" /> คำเตือนความเสี่ยง
        </h3>
        <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink-muted">
          {signal.risks.map((risk) => (
            <li key={risk} className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-loss/80" />
              <span>{risk}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* Watch plan */}
      <section>
        <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-gain">
          <Target className="h-3 w-3" /> แผนเฝ้าดู
        </h3>
        <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink">
          {signal.watch_plan.map((item) => (
            <li key={item} className="flex gap-2">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gain" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* News */}
      {signal.news.length > 0 ? (
        <section>
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
            <Newspaper className="h-3 w-3" /> ข่าวล่าสุด
          </h3>
          <div className="mt-2 flex flex-col gap-2">
            {signal.news.slice(0, 3).map((item, index) => (
              <a
                key={`${item.url}-${index}`}
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="group flex items-start gap-2 rounded-lg border border-bg-line bg-bg-base/40 p-2.5 transition hover:border-gain/30 hover:bg-bg-raised"
              >
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-xs font-medium text-ink group-hover:text-gain">
                    {item.title}
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ink-subtle">
                    <span>{item.source}</span>
                    <span>·</span>
                    <span>{formatRelativeTime(item.published_at)}</span>
                  </div>
                </div>
                <ExternalLink className="h-3 w-3 shrink-0 text-ink-subtle" />
              </a>
            ))}
          </div>
        </section>
      ) : null}

      {/* Summary */}
      {summary ? (
        <section>
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
            <Sparkles className="h-3 w-3" /> สรุปล่าสุด
            <ArrowRight className="h-3 w-3" />
          </h3>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-bg-line bg-bg-base/40 p-3 text-[11px] leading-5 text-ink-muted">
            {summary}
          </pre>
        </section>
      ) : null}
    </div>
  );
}

function EmptyState({
  title,
  description,
  className = ""
}: {
  title: string;
  description: string;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-bg-line bg-bg-surface/40 px-6 py-10 text-center ${className}`}
    >
      <Radar className="h-8 w-8 text-ink-subtle/40" />
      <div className="text-sm font-medium text-ink">{title}</div>
      <div className="text-xs text-ink-muted">{description}</div>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────── */
/*                          HELPERS                              */
/* ──────────────────────────────────────────────────────────── */

const DEFAULT_SCORE_MAX: ScoreBreakdown = {
  momentum: 25,
  volume: 25,
  catalyst: 20,
  premarket_gap: 10,
  breakout: 10,
  risk: 10
};

function formatPercent(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function formatScoreLabel(value: string) {
  const labels: Record<string, string> = {
    momentum: "Momentum",
    volume: "วอลุ่ม",
    catalyst: "ปัจจัยหนุน",
    premarket_gap: "Gap ก่อนเปิดตลาด",
    breakout: "Breakout",
    risk: "ความเสี่ยง"
  };
  return labels[value] ?? value;
}

function formatRelativeTime(iso: string) {
  try {
    const date = new Date(iso);
    const diff = Date.now() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return "เมื่อกี้นี้";
    if (minutes < 60) return `${minutes} นาทีที่แล้ว`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} ชม. ที่แล้ว`;
    const days = Math.floor(hours / 24);
    return `${days} วันที่แล้ว`;
  } catch {
    return iso;
  }
}

function getSurgeScore(signal: Signal) {
  if (typeof signal.surge_score === "number") {
    return signal.surge_score;
  }
  return (
    signal.score +
    Math.max(0, signal.premarket_gap_percent) * 2 +
    signal.relative_volume * 4 +
    signal.score_breakdown.catalyst
  );
}

function getRecommendation(signal: Signal) {
  if (signal.recommendation) {
    return {
      label: signal.recommendation.label,
      shortLabel: signal.recommendation.short_label,
      reason: signal.recommendation.reason,
      className: recommendationClass(signal.recommendation.level),
      levelTag: (["strong", "watch", "caution"].includes(signal.recommendation.level)
        ? signal.recommendation.level
        : "caution") as "strong" | "watch" | "caution"
    };
  }

  const hasStrongMove = signal.premarket_gap_percent >= 5;
  const hasVolume = signal.relative_volume >= 1.5;
  const hasCatalyst = signal.score_breakdown.catalyst >= 8;

  if (signal.score >= 80 && hasStrongMove && hasVolume && hasCatalyst) {
    return {
      label: "น่าเฝ้าดูมาก",
      shortLabel: "เด่นมาก",
      reason: "momentum, วอลุ่ม และข่าวหนุนสอดคล้องกัน เหมาะสำหรับเฝ้าดู confirmation",
      className: "text-gain",
      levelTag: "strong" as const
    };
  }

  if (signal.score >= 60 || (hasStrongMove && hasCatalyst)) {
    return {
      label: "น่าสนใจ แต่ต้องรอยืนยัน",
      shortLabel: "รอยืนยัน",
      reason: "มีแรงเคลื่อนไหวหรือ catalyst ชัด แต่ควรดูวอลุ่มและแนวรับแนวต้านเพิ่ม",
      className: "text-amber",
      levelTag: "watch" as const
    };
  }

  return {
    label: "เฝ้าดูแบบระมัดระวัง",
    shortLabel: "ระวัง",
    reason: "ยังมีบางเงื่อนไขไม่ครบ เช่น วอลุ่มไม่เด่นหรือคะแนนรวมยังไม่สูง",
    className: "text-loss",
    levelTag: "caution" as const
  };
}

function recommendationClass(level: string) {
  if (level === "strong") return "text-gain";
  if (level === "watch") return "text-amber";
  return "text-loss";
}

function formatUniverse(status: SettingsStatus | null, stockCount: number) {
  if (!status) return "—";
  if (status.universe_mode === "all") {
    return stockCount ? stockCount.toLocaleString() : "All";
  }
  return "Sample";
}

function formatAiProvider(status: SettingsStatus | null) {
  if (!status?.ai_configured) return "Scoring";
  if (status.ai_provider === "gemini") return "Gemini";
  if (status.ai_provider === "openai") return "OpenAI";
  return status.ai_provider.toUpperCase();
}
