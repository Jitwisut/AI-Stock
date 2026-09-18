"use client";

import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  BrainCircuit,
  FileText,
  Gauge,
  Loader2,
  Newspaper,
  Search,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingUp
} from "lucide-react";
import { FormEvent, Suspense, useCallback, useEffect, useRef, useState } from "react";

import { DataSourceBadge, Delta, ScoreBadge } from "../../components/Atoms";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

type Recommendation = {
  label: string;
  short_label: string;
  reason: string;
  level: string;
};

type SurgePlan = {
  bias: string;
  note: string;
  entry_zone?: number | null;
  key_high?: number | null;
  stop?: number | null;
  stop_pct?: number | null;
  entry_note?: string | null;
  style: string;
};

type Signal = {
  symbol: string;
  score: number;
  premarket_gap_percent: number;
  relative_volume: number;
  latest_price: number;
  data_source?: string;
  previous_close: number;
  day_high: number;
  prior_high: number;
  catalyst: string;
  reason: string;
  risks: string[];
  watch_plan: string[];
  score_breakdown: ScoreBreakdown;
  surge_score: number;
  surge_plan?: SurgePlan | null;
  recommendation?: Recommendation | null;
  news: NewsItem[];
  provider_updated_at?: string | null;
};

type Stock = {
  symbol: string;
  name: string;
  exchange: string;
  sector?: string | null;
  market_cap?: number | null;
};

type StockAnalysis = {
  query: string;
  analysis_source: "gemini" | "openai" | "rules" | string;
  stock: Stock;
  signal: Signal;
  analysis: string;
  disclaimer: string;
};

const EXAMPLES = ["MTEN", "CHAI", "VSME", "HUBC", "CUPR"];

export default function AnalyzePage() {
  return (
    <Suspense fallback={<AnalyzeFallback />}>
      <AnalyzePageContent />
    </Suspense>
  );
}

function AnalyzePageContent() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("query")?.trim().toUpperCase() || "MTEN";
  const [query, setQuery] = useState(initialQuery);
  const [analysis, setAnalysis] = useState<StockAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const autoSubmittedRef = useRef("");

  const submitAnalysis = useCallback(async (event?: FormEvent<HTMLFormElement>, nextQuery = query) => {
    event?.preventDefault();
    const value = nextQuery.trim().toUpperCase();
    if (!value) return;

    setQuery(value);
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `${API_BASE_URL}/analysis/stock?query=${encodeURIComponent(value)}`,
        { cache: "no-store" }
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = payload?.detail || "";
        throw new Error(
          response.status === 503
            ? `ไม่มีข้อมูลราคาจริงสำหรับ ${value} ในตอนนี้`
            : detail || "วิเคราะห์หุ้นไม่สำเร็จ"
        );
      }
      setAnalysis((await response.json()) as StockAnalysis);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "วิเคราะห์หุ้นไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const symbol = searchParams.get("query")?.trim().toUpperCase();
    if (!symbol || autoSubmittedRef.current === symbol) return;

    autoSubmittedRef.current = symbol;
    setAnalysis(null);
    void submitAnalysis(undefined, symbol);
  }, [searchParams, submitAnalysis]);

  function selectExample(symbol: string) {
    setQuery(symbol);
    void submitAnalysis(undefined, symbol);
  }

  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-subtle">
            รายตัว
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            วิเคราะห์หุ้นรายตัว
          </h1>
          <p className="mt-1.5 max-w-xl text-sm leading-6 text-ink-muted">
            เจาะราคา โมเมนตัม ข่าว ความเสี่ยง และแผนเฝ้าดูเป็นภาษาไทย
          </p>
        </div>

        <form onSubmit={(event) => void submitAnalysis(event)} className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[420px]">
          <div className="flex h-11 overflow-hidden rounded-xl border border-bg-line bg-bg-raised/80 focus-within:ring-2 focus-within:ring-accent-cyan/40">
            <div className="flex w-10 items-center justify-center text-ink-subtle">
              <Search className="h-4 w-4" />
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="เช่น NVDA หรือ NVIDIA"
              className="min-w-0 flex-1 bg-transparent px-1 text-sm text-ink outline-none placeholder:text-ink-subtle"
            />
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="focus-ring inline-flex min-w-28 items-center justify-center gap-2 bg-gain px-4 text-sm font-semibold text-bg-base transition hover:bg-gain-soft disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              วิเคราะห์
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((symbol) => (
              <button
                key={symbol}
                type="button"
                onClick={() => selectExample(symbol)}
                className="chip text-ink-muted transition hover:border-accent-cyan/40 hover:text-accent-cyan"
              >
                {symbol}
              </button>
            ))}
          </div>
        </form>
      </header>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      {!analysis && !loading ? (
        <section className="panel flex min-h-[420px] flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <BrainCircuit className="h-10 w-10 text-accent-cyan/70" />
          <div className="text-base font-semibold text-ink">เลือกหุ้นที่อยากเจาะลึก</div>
          <div className="max-w-xl text-sm leading-6 text-ink-muted">
            ระบบจะดึงราคา ข่าวล่าสุด scoring และสรุปเป็นบทวิเคราะห์ภาษาไทย โดยไม่ใช้ถ้อยคำชี้นำให้ซื้อขายทันที
          </div>
        </section>
      ) : null}

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-[0.9fr_1.4fr]">
          <div className="panel h-80 skeleton" />
          <div className="panel h-80 skeleton" />
        </div>
      ) : null}

      {analysis && !loading ? <AnalysisResult data={analysis} /> : null}
    </main>
  );
}

function AnalyzeFallback() {
  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <div className="panel h-36 skeleton" />
      <div className="mt-5 grid gap-4 lg:grid-cols-[0.9fr_1.4fr]">
        <div className="panel h-80 skeleton" />
        <div className="panel h-80 skeleton" />
      </div>
    </main>
  );
}

function AnalysisResult({ data }: { data: StockAnalysis }) {
  const { stock, signal } = data;
  const sourceLabel = formatAnalysisSource(data.analysis_source);

  return (
    <div className="grid gap-4 lg:grid-cols-[0.9fr_1.4fr]">
      <aside className="space-y-4">
        <section className="panel p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-mono text-2xl font-semibold text-ink">{stock.symbol}</div>
              <div className="mt-1 text-sm text-ink-muted">{stock.name}</div>
              <div className="mt-2 flex flex-wrap gap-2">
                <span className="chip text-ink-muted">{stock.exchange}</span>
                {stock.sector ? <span className="chip text-ink-muted">{stock.sector}</span> : null}
                <span className="chip border-accent-cyan/30 bg-accent-cyan/10 text-accent-cyan">
                  {sourceLabel}
                </span>
                <DataSourceBadge source={signal.data_source} />
              </div>
            </div>
            <ScoreBadge score={signal.score} size="lg" showLabel />
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3">
            <Metric label="ราคาล่าสุด" value={`$${signal.latest_price.toFixed(2)}`} icon={<TrendingUp className="h-4 w-4" />} />
            <Metric label="เปลี่ยนแปลง" value={<Delta value={signal.premarket_gap_percent} />} icon={<Gauge className="h-4 w-4" />} />
            <Metric label="Rel. volume" value={`${signal.relative_volume.toFixed(1)}x`} icon={<Gauge className="h-4 w-4" />} />
            <Metric label="Surge" value={Math.round(signal.surge_score).toString()} icon={<Target className="h-4 w-4" />} />
          </div>
        </section>

        <section className="panel p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <ShieldAlert className="h-4 w-4 text-amber" />
            ความเสี่ยง
          </div>
          <ul className="space-y-2 text-sm leading-6 text-ink-muted">
            {signal.risks.map((risk) => (
              <li key={risk} className="rounded-lg border border-bg-line bg-bg-raised/50 px-3 py-2">
                {risk}
              </li>
            ))}
          </ul>
        </section>

        <section className="panel p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <Newspaper className="h-4 w-4 text-accent-cyan" />
            ข่าวล่าสุด
          </div>
          <div className="space-y-3">
            {signal.news.length ? (
              signal.news.slice(0, 3).map((item) => (
                <a
                  key={`${item.source}-${item.title}`}
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded-lg border border-bg-line bg-bg-raised/50 px-3 py-2 transition hover:border-accent-cyan/40"
                >
                  <div className="line-clamp-2 text-sm font-medium text-ink">{item.title}</div>
                  <div className="mt-1 text-xs text-ink-subtle">{item.source}</div>
                </a>
              ))
            ) : (
              <div className="text-sm text-ink-muted">ยังไม่พบข่าวล่าสุด</div>
            )}
          </div>
        </section>
      </aside>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-bg-line px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <FileText className="h-4 w-4 text-gain" />
            บทวิเคราะห์
          </div>
          {signal.recommendation ? (
            <span className="chip border-gain/30 bg-gain/10 text-gain">
              {signal.recommendation.short_label}
            </span>
          ) : null}
        </div>
        <div className="space-y-5 px-4 py-4">
          <AnalysisText text={data.analysis} />

          {signal.surge_plan ? (
            <div className="rounded-xl border border-bg-line bg-bg-raised/50 p-4">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
                <Target className="h-4 w-4 text-amber" />
                โซนอ้างอิง
              </div>
              <div className="grid gap-2 text-sm text-ink-muted sm:grid-cols-2">
                <div>Bias: {signal.surge_plan.bias}</div>
                <div>Key high: {formatPrice(signal.surge_plan.key_high)}</div>
                <div>Entry zone: {formatPrice(signal.surge_plan.entry_zone)}</div>
                <div>Stop: {formatPrice(signal.surge_plan.stop)}</div>
              </div>
              <p className="mt-3 text-sm leading-6 text-ink-muted">{signal.surge_plan.entry_note}</p>
            </div>
          ) : null}

          <div className="rounded-xl border border-amber/30 bg-amber/10 px-4 py-3 text-sm leading-6 text-amber">
            {data.disclaimer}
          </div>
        </div>
      </section>
    </div>
  );
}

function formatAnalysisSource(source: string) {
  // "gemini:<รุ่น>" = รุ่นที่ตอบจริง (อาจเป็นรุ่นสำรองถ้ารุ่นหลักโดนจำกัดโควตา)
  if (source.startsWith("gemini:")) return `Gemini · ${source.slice(7)}`;
  if (source === "gemini") return "Gemini";
  if (source === "openai") return "OpenAI";
  if (source === "ai") return "AI";
  if (source === "rules") return "Rules";
  return source.toUpperCase();
}

function Metric({
  label,
  value,
  icon
}: {
  label: string;
  value: string | JSX.Element;
  icon: JSX.Element;
}) {
  return (
    <div className="rounded-xl border border-bg-line bg-bg-raised/60 p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-ink-subtle">
        {icon}
        {label}
      </div>
      <div className="stat-number text-base font-semibold text-ink">{value}</div>
    </div>
  );
}

function AnalysisText({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-7 text-ink-muted">
      {text.split("\n").map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={index} className="h-1" />;
        if (!trimmed.startsWith("-") && trimmed.length < 64) {
          return (
            <h2 key={index} className="pt-1 text-base font-semibold text-ink">
              {trimmed}
            </h2>
          );
        }
        return (
          <p key={index} className={trimmed.startsWith("-") ? "pl-3" : ""}>
            {trimmed}
          </p>
        );
      })}
    </div>
  );
}

function formatPrice(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "—";
}
