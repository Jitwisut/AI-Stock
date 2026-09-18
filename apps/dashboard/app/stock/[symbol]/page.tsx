"use client";

import Link from "next/link";
import { AlertTriangle, ArrowLeft, Bot, Clock, ExternalLink, RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Bar as ChartBar, PriceVolumeChart } from "../../../components/PriceVolumeChart";
import {
  AiExplanation,
  COMPONENT_LABEL,
  CONDITION_TONE,
  CONFIDENCE_TONE,
  NA,
  RISK_TONE,
  SourcedField,
  StockAnalysis,
  TREND_TONE,
  UnavailableInfo,
  fmtAge,
  fmtBig,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtTime,
  fmtVolume,
  getWithStatus
} from "../../../lib/engine";

const RANGES = ["1D", "5D", "1M", "3M", "6M", "1Y", "5Y"] as const;
type Range = (typeof RANGES)[number];

type ChartPayload = {
  range: Range;
  interval: string;
  bars?: (ChartBar & { sma20?: number | null; sma50?: number | null; sma200?: number | null })[];
  points?: { t: number; time_et: string; price: number }[];
  note?: string | null;
  market_timestamp?: string | null;
  freshness?: { last_bar_date: string };
};

export default function StockDetailPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol).toUpperCase();
  const [analysis, setAnalysis] = useState<StockAnalysis | null>(null);
  const [unavailable, setUnavailable] = useState<UnavailableInfo | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<Range>("6M");
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [chartError, setChartError] = useState("");
  const [ai, setAi] = useState<AiExplanation | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    setUnavailable(null);
    const result = await getWithStatus<StockAnalysis>(`/stocks/${symbol}/analysis`);
    if (result.data) setAnalysis(result.data);
    if (result.unavailable) setUnavailable(result.unavailable);
    if (result.error) setError(result.error);
    setLoading(false);
  }

  async function loadAi(refresh = false) {
    setAiLoading(true);
    const result = await getWithStatus<AiExplanation>(`/stocks/${symbol}/ai${refresh ? "?refresh=true" : ""}`, 150_000);
    setAi(result.data ?? { status: "unavailable", reason: result.error ?? result.unavailable?.detail });
    setAiLoading(false);
  }

  useEffect(() => {
    void load();
    void loadAi();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  useEffect(() => {
    setChartError("");
    getWithStatus<ChartPayload>(`/stocks/${symbol}/chart?range=${range}`).then((result) => {
      if (result.data) setChart(result.data);
      else setChartError(result.unavailable ? `${result.unavailable.detail} (${result.unavailable.provider})` : result.error ?? "โหลดกราฟไม่สำเร็จ");
    });
  }, [symbol, range]);

  if (loading && !analysis) {
    return <Shell symbol={symbol}><div className="panel p-10 text-center text-ink-subtle">กำลังดึงข้อมูลจริงและคำนวณ...</div></Shell>;
  }
  if (unavailable) {
    return (
      <Shell symbol={symbol}>
        <div className="panel border-amber/40 p-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-amber"><AlertTriangle className="h-5 w-5" /> Data temporarily unavailable</h2>
          <dl className="mt-3 grid gap-1 text-sm text-ink-muted">
            <div>Provider: <b className="text-ink">{unavailable.provider}</b></div>
            <div>Last successful update: {fmtTime(unavailable.last_successful_update)}</div>
            <div>Retry status: {unavailable.retry_status}{unavailable.retry_after_seconds ? ` (ลองใหม่ใน ${unavailable.retry_after_seconds} วินาที)` : ""}</div>
            <div className="text-xs text-ink-subtle">{unavailable.error}</div>
          </dl>
          <button onClick={() => void load()} className="chip mt-4 hover:text-ink"><RefreshCw className="h-3 w-3" /> ลองใหม่</button>
        </div>
      </Shell>
    );
  }
  if (error || !analysis) {
    return <Shell symbol={symbol}><div className="panel p-6 text-loss">{error || "ไม่พบข้อมูล"}</div></Shell>;
  }

  const a = analysis;
  const t = a.technical;
  const quotePrice = a.quote?.price.value;
  const quoteChange = a.quote?.change_percent.value;

  return (
    <Shell symbol={symbol}>
      {/* ─────────── Header ─────────── */}
      <section className="panel mb-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-3xl font-semibold text-ink">{a.symbol}</h1>
              <span className={`chip ${RISK_TONE[a.risk.level] ?? ""}`}><ShieldAlert className="h-3 w-3" /> Risk {a.risk.level}</span>
              <span className={`chip ${CONFIDENCE_TONE[a.confidence.level] ?? ""}`}>Confidence {a.confidence.level}</span>
            </div>
            <div className="mt-1 text-sm text-ink-muted">{a.company_name ?? NA} · {a.exchange ?? NA} · {a.currency}</div>
            <div className="mt-1 text-xs text-ink-subtle">
              {String(a.fundamentals?.sector?.value ?? NA)} / {String(a.fundamentals?.industry?.value ?? NA)} · Market cap {fmtBig(a.fundamentals?.market_cap?.value)}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-3xl font-semibold text-ink">{fmtMoney(quotePrice ?? t.price)}</div>
            <div className={`font-mono text-sm ${typeof quoteChange === "number" && quoteChange < 0 ? "text-loss" : "text-gain"}`}>
              {fmtPct(quoteChange)} {a.quote ? "" : "(quote unavailable — แสดงราคาปิด EOD)"}
            </div>
            <div className="mt-1 text-[11px] text-ink-subtle">
              {a.quote ? (
                <>
                  {a.quote.is_realtime ? "Real-time" : "Delayed"} · {a.quote.delay_note} · {fmtTime(a.quote.market_timestamp, "et")}
                </>
              ) : (
                <>ราคาปิด {t.date}</>
              )}
            </div>
            <div className="text-[11px] text-ink-subtle">{a.data_freshness.market_session.label}</div>
          </div>
        </div>
        <FreshnessBar a={a} ai={ai} />
        {a.unavailable.length ? (
          <div className="mt-3 rounded-xl border border-amber/30 bg-amber/10 p-3 text-xs text-amber">
            ส่วนที่ดึงไม่ได้ (แสดง N/A แทน ไม่ได้เดาค่า):{" "}
            {a.unavailable.map((item) => `${item.part} [${item.provider}]`).join(", ")}
          </div>
        ) : null}
      </section>

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="min-w-0 space-y-4">
          {/* ─────────── Chart ─────────── */}
          <section className="panel p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-ink">กราฟราคา</h2>
              <div className="flex gap-1">
                {RANGES.map((item) => (
                  <button
                    key={item}
                    onClick={() => setRange(item)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium ${range === item ? "bg-gain text-bg-base" : "text-ink-muted hover:bg-bg-raised"}`}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
            {chartError ? (
              <p className="py-10 text-center text-sm text-amber">{chartError}</p>
            ) : chart && chart.range === range ? (
              <>
                <PriceVolumeChart
                  bars={
                    chart.points
                      ? chart.points.map((point) => ({ date: point.time_et, close: point.price, volume: null }))
                      : chart.bars ?? []
                  }
                  height={260}
                  periodLabel={chart.points ? "ตั้งแต่ 4:00 ET (รวม pre-market) ถึงล่าสุด" : undefined}
                />
                <p className="mt-2 text-[11px] text-ink-subtle">
                  {chart.interval === "1m" ? `Intraday 1 นาที · ${chart.market_timestamp ?? ""}` : `แท่งรายวัน (EOD) · ล่าสุด ${chart.freshness?.last_bar_date ?? NA}`}
                  {chart.note ? ` · ${chart.note}` : ""} · {a.data_freshness.price_eod.adjustment}
                </p>
              </>
            ) : (
              <div className="skeleton h-[260px] rounded-xl" />
            )}
          </section>

          {/* ─────────── Score ─────────── */}
          <section className="panel p-4">
            <div className="mb-3 flex items-end justify-between">
              <div>
                <h2 className="text-sm font-semibold text-ink">Opportunity Score</h2>
                <p className="text-[11px] text-ink-subtle">คำนวณจาก algorithm (กลยุทธ์ {a.strategy.name}) — AI ไม่มีสิทธิ์แก้คะแนน</p>
              </div>
              <div className="font-mono text-3xl font-semibold text-ink">
                {a.opportunity_score.total.toFixed(1)}<span className="text-base text-ink-subtle">/100</span>
              </div>
            </div>
            <div className="space-y-2">
              {Object.entries(a.components).map(([key, comp]) => {
                const part = a.opportunity_score.breakdown[key];
                return (
                  <details key={key} className="rounded-xl bg-bg-base/40 px-3 py-2">
                    <summary className="flex cursor-pointer items-center justify-between gap-3 text-sm">
                      <span className="text-ink">{COMPONENT_LABEL[key]}</span>
                      <span className="flex items-center gap-3">
                        <span className="h-1.5 w-28 rounded-full bg-bg-line">
                          <span className="block h-1.5 rounded-full bg-gain" style={{ width: `${comp.max ? (comp.score / comp.max) * 100 : 0}%` }} />
                        </span>
                        <span className="w-20 text-right font-mono text-xs text-ink-muted">
                          {part ? `${part.points.toFixed(1)}/${part.weight.toFixed(0)}` : `${comp.score}/${comp.max}`}
                        </span>
                      </span>
                    </summary>
                    <table className="mt-2 w-full text-xs">
                      <tbody>
                        {comp.rules.map((rule) => (
                          <tr key={rule.rule} className="border-t border-bg-line/60">
                            <td className="py-1 pr-2 text-ink-muted">{rule.rule}</td>
                            <td className="py-1 pr-2 text-right font-mono text-ink-subtle">{formatRuleValue(rule.value)}</td>
                            <td className={`py-1 text-right font-mono ${rule.points < 0 ? "text-loss" : "text-ink"}`}>
                              {rule.points > 0 ? "+" : ""}{rule.points}{rule.max ? `/${rule.max}` : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {key === "fundamental" && typeof comp.coverage === "number" ? (
                      <p className="mt-1 text-[11px] text-ink-subtle">ข้อมูลพื้นฐานครบ {(comp.coverage * 100).toFixed(0)}% — field ที่ไม่มีได้ 0 คะแนน (ไม่ให้คะแนนฟรี)</p>
                    ) : null}
                  </details>
                );
              })}
            </div>
          </section>

          {/* ─────────── Signals ─────────── */}
          <section className="panel p-4">
            <h2 className="mb-3 text-sm font-semibold text-ink">สัญญาณที่ตรวจพบ (พร้อมหลักฐาน)</h2>
            {a.signals.length === 0 ? (
              <p className="text-sm text-ink-subtle">ไม่พบสัญญาณพิเศษในรอบนี้</p>
            ) : (
              <div className="grid gap-2 md:grid-cols-2">
                {a.signals.map((signal) => (
                  <div key={signal.id} className={`rounded-xl border p-3 ${signal.direction === "bullish" ? "border-gain/25 bg-gain/5" : "border-loss/25 bg-loss/5"}`}>
                    <div className={`text-sm font-semibold ${signal.direction === "bullish" ? "text-gain" : "text-loss"}`}>{signal.name}</div>
                    <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
                      {signal.evidence.map((line) => <li key={line}>• {line}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ─────────── Technical + Volume + Momentum ─────────── */}
          <section className="grid gap-4 md:grid-cols-2">
            <div className="panel p-4">
              <h2 className="mb-2 text-sm font-semibold text-ink">Technical Indicators <span className="text-[11px] font-normal text-ink-subtle">(ณ ราคาปิด {t.date})</span></h2>
              <KV rows={[
                ["SMA20 / SMA50 / SMA200", `${fmtNum(t.sma20)} / ${fmtNum(t.sma50)} / ${fmtNum(t.sma200)}`],
                ["EMA20 / EMA50", `${fmtNum(t.ema20)} / ${fmtNum(t.ema50)}`],
                ["RSI 14", fmtNum(t.rsi14, 1)],
                ["MACD / Signal / Hist", `${fmtNum(t.macd, 3)} / ${fmtNum(t.macd_signal, 3)} / ${fmtNum(t.macd_hist, 3)}`],
                ["ATR 14 (% ของราคา)", `${fmtNum(t.atr14)} (${fmtPct(t.atr_percent, 2, false)})`],
                ["Bollinger (20,2)", `${fmtNum(t.bb_lower)} – ${fmtNum(t.bb_upper)} · %B ${fmtNum(t.bb_percent_b, 2)}`],
                ["ห่าง SMA50 / SMA200", `${fmtPct(t.distance_sma50_percent)} / ${fmtPct(t.distance_sma200_percent)}`],
                ["ความผันผวน 60D (ต่อปี)", fmtPct(t.volatility_60d_annualized_percent, 1, false)],
                ["Max drawdown 1Y", fmtPct(t.max_drawdown_1y_percent, 1)],
                ["RS 3M vs SPY", t.relative_strength_3m_vs_spy == null ? NA : `${fmtNum(t.relative_strength_3m_vs_spy, 2)} pp`]
              ]} />
            </div>
            <div className="space-y-4">
              <div className="panel p-4">
                <h2 className="mb-2 text-sm font-semibold text-ink">Volume Analysis</h2>
                <KV rows={[
                  ["วอลุ่มวันล่าสุด", fmtVolume(t.volume)],
                  ["ค่าเฉลี่ย 20 วัน (ก่อนหน้า)", fmtVolume(t.avg_volume_20d)],
                  ["Volume ratio", t.volume_ratio == null ? NA : `${fmtNum(t.volume_ratio)}x`],
                  ["มูลค่าซื้อขายเฉลี่ย 20D", fmtBig(t.avg_dollar_volume_20d)],
                  ["Up/Down volume 20D", fmtNum(t.up_down_volume_ratio_20d)],
                  ["Nasdaq “Average Volume”", fmtVolume(a.fundamentals?.average_volume_provider?.value)]
                ]} />
              </div>
              <div className="panel p-4">
                <h2 className="mb-2 text-sm font-semibold text-ink">Momentum & Multi-Timeframe</h2>
                <div className="mb-2 grid grid-cols-6 gap-1 text-center text-[11px]">
                  {["1d", "5d", "1m", "3m", "6m", "1y"].map((key) => (
                    <div key={key} className="rounded-lg bg-bg-base/40 py-1">
                      <div className="text-ink-subtle">{key.toUpperCase()}</div>
                      <div className={`font-mono ${(a.multi_timeframe.returns[key] ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(a.multi_timeframe.returns[key], 1)}</div>
                    </div>
                  ))}
                </div>
                {(["short_term", "medium_term", "long_term"] as const).map((key) => {
                  const item = a.multi_timeframe[key];
                  return (
                    <div key={key} className="border-t border-bg-line/60 py-1.5 text-xs">
                      <span className="text-ink-muted">{item.horizon}: </span>
                      <b className={TREND_TONE[item.trend] ?? ""}>{item.trend}</b>
                      <div className="text-[11px] text-ink-subtle">{item.evidence}</div>
                    </div>
                  );
                })}
                {a.multi_timeframe.note ? <p className="mt-1 text-xs text-amber">{a.multi_timeframe.note}</p> : null}
              </div>
            </div>
          </section>

          {/* ─────────── Fundamentals ─────────── */}
          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold text-ink">Fundamentals</h2>
            {!a.fundamentals ? (
              <p className="text-sm text-amber">Unavailable — ดึงข้อมูลพื้นฐานไม่ได้ในรอบนี้</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-xs">
                  <thead className="text-left text-[10px] uppercase tracking-wider text-ink-subtle">
                    <tr><th className="py-1">Metric</th><th className="py-1 text-right">Value</th><th className="py-1 pl-3">As of</th><th className="py-1 pl-3">Source</th></tr>
                  </thead>
                  <tbody>
                    {FUNDAMENTAL_ROWS.map(([key, label, kind]) => {
                      const item = a.fundamentals?.[key] as SourcedField<any> | undefined;
                      return (
                        <tr key={key} className="border-t border-bg-line/60">
                          <td className="py-1.5 text-ink-muted">{label}</td>
                          <td className="py-1.5 text-right font-mono text-ink" title={item?.note ?? ""}>{formatFundamental(item?.value, kind)}</td>
                          <td className="py-1.5 pl-3 text-ink-subtle">{item?.as_of ?? "—"}</td>
                          <td className="py-1.5 pl-3 text-[10px] text-ink-subtle">{item?.value == null ? item?.note ?? "N/A" : item?.source}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ─────────── News (FACT) ─────────── */}
          <section className="panel p-4">
            <h2 className="mb-1 text-sm font-semibold text-ink">ข่าว (FACT)</h2>
            <p className="mb-3 text-[11px] text-ink-subtle">หัวข้อ แหล่ง และเวลาเผยแพร่จากแหล่งข่าวจริง — การตีความของ AI แยกอยู่ในกล่อง AI Analysis · ดึงเมื่อ {fmtTime(a.news.fetched_at)}</p>
            {a.news.items.length === 0 ? (
              <p className="text-sm text-ink-subtle">{a.news.status === "unavailable" ? "แหล่งข่าวใช้ไม่ได้ชั่วคราว" : `ไม่พบข่าวภายใน ${a.news.max_age_days} วัน`}</p>
            ) : (
              <ul className="space-y-2">
                {a.news.items.map((item) => (
                  <li key={item.url || item.title} className="rounded-xl bg-bg-base/40 p-2.5">
                    <a href={item.url} target="_blank" rel="noreferrer noopener" className="flex items-start gap-1.5 text-sm text-ink hover:underline">
                      {item.title} <ExternalLink className="mt-1 h-3 w-3 shrink-0 text-ink-subtle" />
                    </a>
                    <div className="mt-0.5 text-[11px] text-ink-subtle">
                      {item.source} · {fmtTime(item.published_at)} · <span className={item.age_hours > 72 ? "text-amber" : ""}>{fmtAge(item.age_hours)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* ─────────── Right column ─────────── */}
        <aside className="space-y-4">
          <AiPanel ai={ai} loading={aiLoading} onRefresh={() => void loadAi(true)} />

          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold text-ink">Risk: <span className={`chip ${RISK_TONE[a.risk.level] ?? ""}`}>{a.risk.level}</span></h2>
            {a.risk.factors.length === 0 ? (
              <p className="text-xs text-ink-subtle">ไม่พบปัจจัยเสี่ยงตามเกณฑ์ (ไม่ได้แปลว่าไม่มีความเสี่ยง)</p>
            ) : (
              <ul className="space-y-1 text-xs text-ink-muted">
                {a.risk.factors.map((factor) => (
                  <li key={factor.factor}>• <b className="text-ink">{factor.factor}</b> (−{factor.penalty}): {factor.explanation}</li>
                ))}
              </ul>
            )}
            {a.days_to_earnings != null ? (
              <p className="mt-2 text-xs text-ink-subtle">ประกาศงบถัดไป: {String(a.fundamentals?.next_earnings_date?.value)} ({a.days_to_earnings} วัน) · {a.fundamentals?.next_earnings_date?.note}</p>
            ) : null}
          </section>

          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold text-ink">Confidence: <span className={`chip ${CONFIDENCE_TONE[a.confidence.level] ?? ""}`}>{a.confidence.level}</span> <span className="text-[11px] font-normal text-ink-subtle">{a.confidence.points}/{a.confidence.max_points}</span></h2>
            <ul className="space-y-1 text-xs text-ink-muted">
              {a.confidence.factors.map((factor) => (
                <li key={factor.factor}>
                  <span className={`font-mono ${factor.points < 0 ? "text-loss" : "text-ink"}`}>{factor.points > 0 ? "+" : ""}{factor.points}</span> {factor.factor}: {factor.explanation}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] text-ink-subtle">คำนวณจากความครบ/ความสดของข้อมูลและความสอดคล้องของสัญญาณ ไม่ได้ให้ AI เดา</p>
          </section>

          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold text-ink">Key Levels</h2>
            <table className="w-full text-xs">
              <tbody>
                {a.key_levels.map((level) => (
                  <tr key={level.name} className="border-t border-bg-line/60">
                    <td className="py-1 text-ink-muted">{level.name}</td>
                    <td className="py-1 text-right font-mono text-ink">{fmtMoney(level.price)}</td>
                    <td className={`py-1 text-right font-mono ${level.type === "resistance" ? "text-loss" : "text-gain"}`}>{fmtPct(level.distance_percent, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel p-4">
            <h2 className="mb-2 flex items-center justify-between text-sm font-semibold text-ink">
              Market Context
              <span className={`chip ${CONDITION_TONE[a.market_context?.condition ?? "Unknown"] ?? ""}`}>{a.market_context?.condition ?? "Unavailable"}</span>
            </h2>
            <ul className="space-y-1 text-xs text-ink-muted">
              {(a.market_context?.evidence ?? []).map((line) => <li key={line}>• {line}</li>)}
            </ul>
            {a.sector_context && a.sector_context.status !== "unavailable" ? (
              <div className="mt-3 border-t border-bg-line pt-2 text-xs text-ink-muted">
                <div>Sector ETF <b className="text-ink">{a.sector_context.etf}</b>: 1M {fmtPct(a.sector_context.return_1m_percent, 1)} · 3M {fmtPct(a.sector_context.return_3m_percent, 1)}</div>
                <div>หุ้นเทียบ sector (1M): <b className="text-ink">{a.sector_context.stock_vs_sector_1m_percent == null ? NA : `${fmtNum(a.sector_context.stock_vs_sector_1m_percent)} pp`}</b></div>
                <div className="mt-1 text-[11px] text-ink-subtle">{a.sector_context.interpretation_basis}</div>
              </div>
            ) : null}
          </section>
        </aside>
      </div>
    </Shell>
  );
}

function Shell({ symbol, children }: { symbol: string; children: React.ReactNode }) {
  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <Link href="/discover" className="mb-4 inline-flex items-center gap-1 text-xs text-ink-subtle hover:text-ink">
        <ArrowLeft className="h-3 w-3" /> กลับไปหน้า Discovery
      </Link>
      <span className="sr-only">{symbol}</span>
      {children}
      <p className="mt-6 text-center text-[11px] text-ink-subtle">ข้อมูลเพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน · ตัวเลขทั้งหมดมาจาก provider ที่ระบุ ค่าที่ไม่มีแสดงเป็น N/A</p>
    </main>
  );
}

function FreshnessBar({ a, ai }: { a: StockAnalysis; ai: AiExplanation | null }) {
  const f = a.data_freshness;
  const items: { label: string; value: string; stale?: boolean }[] = [
    { label: "Price (EOD) updated", value: `${f.price_eod.last_bar_date ?? NA} close`, stale: f.price_eod.is_stale },
    { label: "Quote", value: f.quote.status === "ok" ? `${f.quote.is_realtime ? "real-time" : "delayed"} · ${fmtTime(f.quote.market_timestamp, "et")}` : "Unavailable", stale: f.quote.status !== "ok" },
    { label: "Financial data updated", value: f.financials.period_end ? `FY ending ${f.financials.period_end}` : "Unavailable", stale: f.financials.is_stale || f.financials.status !== "ok" },
    { label: "News updated", value: fmtTime(f.news.fetched_at), stale: f.news.status === "unavailable" },
    { label: "AI analysis generated", value: ai?.generated_at ? fmtTime(ai.generated_at) : ai?.status === "unavailable" ? "Unavailable" : "…" }
  ];
  return (
    <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
      {items.map((item) => (
        <div key={item.label} className={`rounded-xl border px-3 py-2 text-[11px] ${item.stale ? "border-amber/40 bg-amber/10" : "border-bg-line bg-bg-base/40"}`}>
          <div className="flex items-center gap-1 text-ink-subtle"><Clock className="h-3 w-3" /> {item.label}</div>
          <div className="mt-0.5 text-ink">{item.value}</div>
          {item.stale ? <div className="font-semibold text-amber">STALE DATA</div> : null}
        </div>
      ))}
    </div>
  );
}

function AiPanel({ ai, loading, onRefresh }: { ai: AiExplanation | null; loading: boolean; onRefresh: () => void }) {
  const o = ai?.output;
  return (
    <section className="panel border-accent-violet/30 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink"><Bot className="h-4 w-4 text-accent-violet" /> AI Analysis</h2>
        <button onClick={onRefresh} disabled={loading} className="chip hover:text-ink disabled:opacity-50">
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} /> {loading ? "กำลังวิเคราะห์" : "สร้างใหม่"}
        </button>
      </div>
      {loading && !ai ? (
        <p className="text-xs text-ink-subtle">AI กำลังอ่านข้อมูลที่คำนวณแล้ว (อาจใช้ 20-60 วินาที)...</p>
      ) : !ai || ai.status !== "ok" || !o ? (
        <p className="text-xs text-amber">AI unavailable: {ai?.reason ?? "ไม่ทราบสาเหตุ"} — ข้อมูลและคะแนนด้านซ้ายยังถูกต้องครบ เพราะไม่ได้มาจาก AI</p>
      ) : (
        <div className="space-y-3 text-xs text-ink-muted">
          <p className="text-[10px] text-ink-subtle">
            {ai.role} · รุ่นที่ตอบ: <b>{ai.model}</b>{ai.model !== ai.requested_model ? ` (ขอ ${ai.requested_model} แต่ไม่พร้อมใช้งาน)` : ""} · {fmtTime(ai.generated_at)}
          </p>
          {o.summary ? <p className="text-sm text-ink">{o.summary}</p> : null}
          <AiList title="ทำไมน่าสนใจ" items={(o.why_interesting ?? []).map((x) => `${x.point} — [${x.evidence}]`)} />
          <AiList title="Bull case" items={o.bull_case ?? []} tone="text-gain" />
          <AiList title="Bear case" items={o.bear_case ?? []} tone="text-loss" />
          <AiList title="Key risks" items={(o.risk_explanations ?? []).map((x) => `${x.risk}: ${x.explanation}`)} />
          <AiList title="Scenario ที่ควรจับตา" items={(o.scenarios_to_watch ?? []).map((x) => `${x.scenario} → ${x.trigger}`)} />
          {o.sector_vs_company ? <div><div className="mb-0.5 font-semibold text-ink">Sector vs บริษัท</div><p>{o.sector_vs_company}</p></div> : null}
          {o.news_assessment?.length ? (
            <div>
              <div className="mb-1 font-semibold text-ink">ข่าว: FACT vs AI interpretation</div>
              {o.news_assessment.map((item) => (
                <div key={item.fact.title} className="mb-2 rounded-lg bg-bg-base/40 p-2">
                  <div className="text-[11px] text-ink">FACT: {item.fact.title}</div>
                  <div className="text-[10px] text-ink-subtle">{item.fact.source} · {fmtAge(item.fact.age_hours)}</div>
                  <div className="mt-1">
                    <span className={`chip mr-1 ${item.ai_classification === "positive" ? "text-gain" : item.ai_classification === "negative" ? "text-loss" : ""}`}>AI: {item.ai_classification}</span>
                    {item.ai_interpretation}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <AiList title="Insufficient data" items={o.insufficient_data ?? []} tone="text-amber" />
          {ai.validation && ai.validation.removed_count > 0 ? (
            <p className="text-[10px] text-amber">
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              ระบบตัดข้อความ AI ออก {ai.validation.removed_count} รายการ เพราะอ้างตัวเลข/ข่าวที่ไม่มีใน input หรือใช้คำชี้นำ
            </p>
          ) : null}
          <p className="text-[10px] text-ink-subtle">{ai.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

function AiList({ title, items, tone = "text-ink" }: { title: string; items: string[]; tone?: string }) {
  if (!items.length) return null;
  return (
    <div>
      <div className={`mb-0.5 font-semibold ${tone}`}>{title}</div>
      <ul className="space-y-0.5">{items.map((item) => <li key={item}>• {item}</li>)}</ul>
    </div>
  );
}

function KV({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3 border-t border-bg-line/60 py-1.5">
          <dt className="text-ink-muted">{label}</dt>
          <dd className="text-right font-mono text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const FUNDAMENTAL_ROWS: [string, string, "money" | "pct" | "num" | "text" | "big" | "eps"][] = [
  ["market_cap", "Market Cap", "big"],
  ["pe_ratio", "P/E (TTM)", "num"],
  ["forward_pe", "Forward P/E", "num"],
  ["eps_ttm", "EPS (TTM)", "money"],
  ["last_quarter_eps", "EPS ไตรมาสล่าสุด vs consensus", "eps"],
  ["revenue", "Revenue (FY)", "big"],
  ["revenue_growth_percent", "Revenue Growth (YoY)", "pct"],
  ["earnings_growth_percent", "Earnings Growth (YoY)", "pct"],
  ["profit_margin_percent", "Profit Margin", "pct"],
  ["roe_percent", "ROE", "pct"],
  ["debt_to_equity", "Debt to Equity", "num"],
  ["free_cash_flow", "Free Cash Flow (FY)", "big"],
  ["dividend_yield_percent", "Dividend Yield", "pct"],
  ["next_earnings_date", "Next Earnings Date", "text"]
];

function formatFundamental(value: unknown, kind: string): string {
  if (value == null) return NA;
  if (kind === "big") return fmtBig(value);
  if (kind === "pct") return fmtPct(value, 2, false);
  if (kind === "money") return fmtMoney(value);
  if (kind === "num") return fmtNum(value);
  if (kind === "eps" && typeof value === "object") {
    const eps = value as { period: string; actual: number; consensus: number | null; surprise_percent: number | null };
    return `${eps.period}: ${fmtMoney(eps.actual)} vs ${fmtMoney(eps.consensus)} (${fmtPct(eps.surprise_percent, 1)})`;
  }
  return String(value);
}

function formatRuleValue(value: unknown): string {
  if (value == null) return NA;
  if (typeof value === "number") return Math.abs(value) >= 1e6 ? fmtBig(value) : fmtNum(value);
  return String(value);
}
