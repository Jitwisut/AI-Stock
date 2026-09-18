"use client";

import Link from "next/link";
import { AlertTriangle, Filter, Info, Play, RefreshCw, ShieldAlert, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchJson } from "../../lib/api";
import {
  COMPONENT_LABEL,
  CONDITION_TONE,
  CONFIDENCE_TONE,
  DiscoveryPayload,
  DiscoveryResult,
  MarketInfo,
  RISK_TONE,
  RunStatus,
  StrategyInfo,
  TREND_TONE,
  fmtBig,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtTime,
  pollRun,
  postJson
} from "../../lib/engine";

const COMPONENTS = ["trend", "momentum", "volume", "fundamental", "risk", "catalyst"] as const;

const FUNNEL_STEPS: { key: string; label: string }[] = [
  { key: "listed", label: "หุ้นทั้งหมดใน Nasdaq screener" },
  { key: "passed_filters", label: "ผ่านเกณฑ์ราคา/มูลค่าตลาด/ประเภทหุ้น" },
  { key: "deep_scan_selected", label: "เลือกเข้า deep scan (สภาพคล่อง+ตัวที่ขยับแรง+ตัวใหญ่)" },
  { key: "history_fetched", label: "ดึงแท่งราคาจริงได้" },
  { key: "passed_technical_filter", label: "ผ่านเงื่อนไขกลยุทธ์ + สภาพคล่อง 20 วัน" },
  { key: "finalists_for_fundamentals", label: "Finalists (ดึงข้อมูลพื้นฐาน)" },
  { key: "ranked", label: "ผ่านเงื่อนไขพื้นฐาน/ความเสี่ยง และถูกจัดอันดับ" },
  { key: "returned", label: "แสดงผล" }
];

export default function DiscoverPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [markets, setMarkets] = useState<MarketInfo>({});
  const [market, setMarket] = useState("US");
  const [strategy, setStrategy] = useState("momentum");
  const [weights, setWeights] = useState<Record<string, number>>({ trend: 25, momentum: 20, volume: 15, fundamental: 20, risk: 10, catalyst: 10 });
  const [minCap, setMinCap] = useState(300);
  const [minPrice, setMinPrice] = useState(2);
  const [data, setData] = useState<DiscoveryPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchJson<{ strategies: StrategyInfo[]; markets: MarketInfo }>("/discovery/strategies")
      .then((payload) => {
        setStrategies(payload.strategies);
        setMarkets(payload.markets);
      })
      .catch(() => setError("โหลดรายการกลยุทธ์ไม่สำเร็จ"));
  }, []);

  useEffect(() => {
    void loadLatest(strategy);
  }, [strategy]);

  async function loadLatest(strategyId: string) {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchJson<DiscoveryPayload>(`/discovery/latest?strategy=${strategyId}&market=${market}`);
      setData(payload.status === "empty" ? null : payload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "โหลดผลล่าสุดไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  async function runScan() {
    setRunning(true);
    setError("");
    setProgress("ส่งงานสแกน");
    try {
      const query = new URLSearchParams({
        strategy,
        market,
        min_market_cap: String(minCap * 1e6),
        min_price: String(minPrice)
      });
      if (strategy === "custom") {
        for (const key of COMPONENTS) query.set(`w_${key}`, String(weights[key] ?? 0));
      }
      const started = await postJson<RunStatus>(`/discovery/run?${query.toString()}`);
      const finished = await pollRun(`/discovery/runs/${started.id}`, (status) =>
        setProgress(status.progress ?? status.status)
      );
      if (finished.status !== "completed" || !finished.result) {
        throw new Error(finished.error ?? "สแกนไม่สำเร็จ");
      }
      setData({ ...finished.result, is_stale: false });
      setProgress("");
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "สแกนไม่สำเร็จ");
    } finally {
      setRunning(false);
    }
  }

  const selectedStrategy = strategies.find((item) => item.id === strategy);
  const marketInfo = markets[market];
  const marketUnavailable = marketInfo && marketInfo.status !== "supported";
  const weightTotal = useMemo(() => COMPONENTS.reduce((sum, key) => sum + (weights[key] || 0), 0), [weights]);

  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-ink-subtle">AI Stock Discovery</p>
          <h1 className="mt-1 text-2xl font-semibold text-ink sm:text-3xl">ค้นหาหุ้นน่าสนใจทั้งตลาด</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            อันดับมาจากคะแนนที่คำนวณด้วยสูตรตายตัว (deterministic) จากข้อมูลจริง — AI ไม่ได้เลือกหรือเรียงหุ้น
            มีหน้าที่อธิบายเหตุผลในหน้ารายละเอียดหุ้นเท่านั้น
          </p>
        </div>
        <Link href="/backtest" className="chip hover:text-ink">
          <Info className="h-3.5 w-3.5" /> คะแนนนี้ใช้ได้จริงไหม? ดู Backtest
        </Link>
      </header>

      {/* ─────────── Controls ─────────── */}
      <section className="panel mb-6 p-4 sm:p-5">
        <div className="grid gap-4 md:grid-cols-[160px_1fr_auto]">
          <label className="block text-xs text-ink-subtle">
            ตลาด
            <select
              value={market}
              onChange={(event) => setMarket(event.target.value)}
              className="focus-ring mt-1 w-full rounded-xl border border-bg-line bg-bg-raised px-3 py-2 text-sm text-ink"
            >
              {Object.entries(markets).map(([id, info]) => (
                <option key={id} value={id}>
                  {id} {info.status !== "supported" ? "(Unavailable)" : ""}
                </option>
              ))}
              {Object.keys(markets).length === 0 ? <option value="US">US</option> : null}
            </select>
          </label>
          <label className="block text-xs text-ink-subtle">
            กลยุทธ์
            <select
              value={strategy}
              onChange={(event) => setStrategy(event.target.value)}
              className="focus-ring mt-1 w-full rounded-xl border border-bg-line bg-bg-raised px-3 py-2 text-sm text-ink"
            >
              {strategies.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <button
              onClick={() => void runScan()}
              disabled={running || Boolean(marketUnavailable) || (strategy === "custom" && weightTotal <= 0)}
              className="focus-ring inline-flex h-10 items-center gap-2 rounded-xl bg-gain px-5 text-sm font-semibold text-bg-base transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {running ? "กำลังสแกน..." : "สแกนทั้งตลาด"}
            </button>
          </div>
        </div>

        {marketUnavailable ? (
          <p className="mt-3 flex items-start gap-2 rounded-xl border border-amber/30 bg-amber/10 p-3 text-sm text-amber">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {marketInfo?.reason}
          </p>
        ) : null}

        {selectedStrategy ? (
          <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
            <div className="rounded-xl border border-bg-line bg-bg-base/40 p-3">
              <div className="text-ink">{selectedStrategy.description}</div>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-subtle">
                <Filter className="h-3 w-3" /> เงื่อนไข: {selectedStrategy.filter}
              </div>
            </div>
            <div className="rounded-xl border border-bg-line bg-bg-base/40 p-3">
              <div className="mb-2 text-xs text-ink-subtle">น้ำหนักคะแนน (รวม 100)</div>
              {strategy === "custom" ? (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {COMPONENTS.map((key) => (
                    <label key={key} className="text-xs text-ink-muted">
                      {COMPONENT_LABEL[key]}: {weights[key]}
                      <input
                        type="range"
                        min={0}
                        max={50}
                        value={weights[key]}
                        onChange={(event) => setWeights({ ...weights, [key]: Number(event.target.value) })}
                        className="w-full accent-[rgb(var(--gain))]"
                      />
                    </label>
                  ))}
                  <div className="col-span-full text-[11px] text-ink-subtle">
                    รวม {weightTotal} — ระบบจะปรับสัดส่วนให้รวมเป็น 100 อัตโนมัติ
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {COMPONENTS.map((key) => (
                    <span key={key} className="chip">
                      {COMPONENT_LABEL[key]} {selectedStrategy.weights[key] ?? 0}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

        <details className="mt-3 text-xs text-ink-subtle">
          <summary className="cursor-pointer">ตัวกรองขั้นต่ำ</summary>
          <div className="mt-2 flex flex-wrap gap-4">
            <label>
              มูลค่าตลาดขั้นต่ำ (ล้าน USD)
              <input
                type="number"
                min={0}
                value={minCap}
                onChange={(event) => setMinCap(Number(event.target.value))}
                className="focus-ring ml-2 w-28 rounded-lg border border-bg-line bg-bg-raised px-2 py-1 text-ink"
              />
            </label>
            <label>
              ราคาขั้นต่ำ (USD)
              <input
                type="number"
                min={0.5}
                step={0.5}
                value={minPrice}
                onChange={(event) => setMinPrice(Number(event.target.value))}
                className="focus-ring ml-2 w-20 rounded-lg border border-bg-line bg-bg-raised px-2 py-1 text-ink"
              />
            </label>
          </div>
        </details>

        {running && progress ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-ink-muted">
            <RefreshCw className="h-3.5 w-3.5 animate-spin" /> {progress}
            <span className="text-ink-subtle">(สแกนครั้งแรกของวันใช้เวลา 2-5 นาที ครั้งถัดไปเร็วขึ้นเพราะมีแคช)</span>
          </p>
        ) : null}
        {error ? (
          <p className="mt-3 flex items-start gap-2 rounded-xl border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </p>
        ) : null}
      </section>

      {loading ? (
        <div className="panel p-10 text-center text-ink-subtle">กำลังโหลดผลสแกนล่าสุด...</div>
      ) : !data ? (
        <div className="panel p-10 text-center text-ink-subtle">
          ยังไม่มีผลสแกนของกลยุทธ์นี้ — กด “สแกนทั้งตลาด” เพื่อเริ่ม
        </div>
      ) : (
        <DiscoveryView data={data} />
      )}
    </main>
  );
}

function DiscoveryView({ data }: { data: DiscoveryPayload }) {
  const condition = data.market_context?.condition ?? "Unknown";
  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
      <section className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-subtle">
          <span className="chip">ข้อมูล ณ ราคาปิด {data.as_of_session} (EOD)</span>
          {data.finished_at ? <span className="chip">สแกนเมื่อ {fmtTime(data.finished_at)}</span> : null}
          {data.is_stale ? (
            <span className="chip border-amber/40 bg-amber/10 text-amber">
              <AlertTriangle className="h-3 w-3" /> STALE DATA — มี session ใหม่ ({data.expected_last_session}) กดสแกนใหม่
            </span>
          ) : null}
          <span className="chip">{data.ranking_method}</span>
        </div>
        <div className="space-y-3">
          {data.results.length === 0 ? (
            <div className="panel p-8 text-center text-ink-subtle">ไม่มีหุ้นที่ผ่านเงื่อนไขของกลยุทธ์นี้ในรอบนี้</div>
          ) : (
            data.results.map((row) => <ResultCard key={row.symbol} row={row} />)
          )}
        </div>
      </section>

      <aside className="space-y-4">
        <div className="panel p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">สภาวะตลาด (US)</h2>
            <span className={`chip ${CONDITION_TONE[condition] ?? CONDITION_TONE.Unknown}`}>{condition}</span>
          </div>
          <ul className="space-y-1 text-xs text-ink-muted">
            {(data.market_context?.evidence ?? []).map((line) => (
              <li key={line}>• {line}</li>
            ))}
          </ul>
        </div>

        <div className="panel p-4">
          <h2 className="mb-3 text-sm font-semibold text-ink">Funnel การคัดกรอง</h2>
          <ol className="space-y-2 text-xs">
            {FUNNEL_STEPS.map((step) =>
              data.funnel[step.key] !== undefined ? (
                <li key={step.key} className="flex items-center justify-between gap-3">
                  <span className="text-ink-muted">{step.label}</span>
                  <span className="font-mono font-semibold text-ink">{Number(data.funnel[step.key]).toLocaleString()}</span>
                </li>
              ) : null
            )}
          </ol>
          <div className="mt-3 space-y-1 border-t border-bg-line pt-3 text-[11px] text-ink-subtle">
            <div>ดึงประวัติไม่สำเร็จ: {String(data.funnel.history_failed ?? 0)} · ข้อมูลเก่าถูกตัด: {String(data.funnel.stale_data_excluded ?? 0)}</div>
            <div>ถูก rate limit: {String(data.funnel.rate_limited ?? 0)} · ใช้เวลา {String(data.funnel.duration_seconds ?? "-")} วินาที</div>
          </div>
        </div>

        <div className="panel p-4 text-[11px] leading-relaxed text-ink-subtle">
          {data.notes.map((note) => (
            <p key={note} className="mb-1">• {note}</p>
          ))}
          <p className="mt-2 text-ink-muted">ข้อมูลเพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน</p>
        </div>
      </aside>
    </div>
  );
}

function ResultCard({ row }: { row: DiscoveryResult }) {
  const bullish = row.signals.filter((signal) => signal.direction === "bullish");
  const bearish = row.signals.filter((signal) => signal.direction === "bearish");
  return (
    <Link href={`/stock/${row.symbol}`} className="panel panel-interactive block p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="mt-1 w-6 text-right font-mono text-xs text-ink-subtle">#{row.rank}</span>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-lg font-semibold text-ink">{row.symbol}</span>
              <span className={`chip ${RISK_TONE[row.risk.level] ?? ""}`}>
                <ShieldAlert className="h-3 w-3" /> Risk {row.risk.level}
              </span>
              <span className={`chip ${CONFIDENCE_TONE[row.confidence.level] ?? ""}`}>Confidence {row.confidence.level}</span>
            </div>
            <div className="max-w-[420px] truncate text-xs text-ink-subtle">
              {row.name} · {row.sector ?? "N/A"} · {fmtBig(row.market_cap)}
            </div>
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-semibold text-ink">
            {row.opportunity_score.total.toFixed(1)}
            <span className="text-sm text-ink-subtle">/100</span>
          </div>
          <div className="text-[11px] text-ink-subtle">
            {fmtMoney(row.price)} ปิด {row.price_date} · 1D {fmtPct(row.returns["1d"])}
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {COMPONENTS.map((key) => {
          const part = row.opportunity_score.breakdown[key];
          if (!part) return null;
          const ratio = part.weight ? part.points / part.weight : 0;
          return (
            <div key={key} className="rounded-lg bg-bg-base/40 px-2 py-1.5">
              <div className="flex justify-between text-[10px] text-ink-subtle">
                <span>{COMPONENT_LABEL[key]}</span>
                <span className="font-mono">
                  {part.points.toFixed(1)}/{part.weight.toFixed(0)}
                </span>
              </div>
              <div className="mt-1 h-1 rounded-full bg-bg-line">
                <div className="h-1 rounded-full bg-gain" style={{ width: `${Math.round(ratio * 100)}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {bullish.map((signal) => (
          <span key={signal.id} className="chip border-gain/30 bg-gain/10 text-gain" title={signal.evidence.join("\n")}>
            <Sparkles className="h-3 w-3" /> {signal.name}
          </span>
        ))}
        {bearish.map((signal) => (
          <span key={signal.id} className="chip border-loss/30 bg-loss/10 text-loss" title={signal.evidence.join("\n")}>
            {signal.name}
          </span>
        ))}
      </div>

      <div className="mt-3 grid gap-x-4 gap-y-1 text-[11px] text-ink-muted sm:grid-cols-2 lg:grid-cols-4">
        <span>
          เทรนด์ S/M/L:{" "}
          <b className={TREND_TONE[row.trend.short_term]}>{row.trend.short_term}</b> /{" "}
          <b className={TREND_TONE[row.trend.medium_term]}>{row.trend.medium_term}</b> /{" "}
          <b className={TREND_TONE[row.trend.long_term]}>{row.trend.long_term}</b>
        </span>
        <span>RSI14 {fmtNum(row.metrics.rsi14, 1)} · Vol {fmtNum(row.metrics.volume_ratio, 2)}x</span>
        <span>1M {fmtPct(row.returns["1m"], 1)} · 3M {fmtPct(row.returns["3m"], 1)}</span>
        <span>
          Rev growth {fmtPct(row.fundamentals?.revenue_growth_percent, 1)} · P/E {fmtNum(row.fundamentals?.pe_ratio, 1)}
        </span>
      </div>
      {row.risk.factors.length ? (
        <div className="mt-2 text-[11px] text-ink-subtle">ความเสี่ยง: {row.risk.factors.slice(0, 3).join(" · ")}</div>
      ) : null}
      {row.freshness.is_stale ? (
        <div className="mt-2 text-[11px] text-amber">STALE DATA — แท่งล่าสุด {row.freshness.last_bar_date}</div>
      ) : null}
    </Link>
  );
}
