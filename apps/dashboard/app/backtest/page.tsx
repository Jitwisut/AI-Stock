"use client";

import { AlertTriangle, FlaskConical, Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchJson } from "../../lib/api";
import { fmtPct, fmtTime, pollRun, postJson } from "../../lib/engine";

type Stats = {
  n: number;
  win_rate_percent?: number;
  avg_return_percent?: number;
  median_return_percent?: number;
  avg_excess_vs_spy_percent?: number | null;
  avg_max_drawdown_percent?: number | null;
  worst_drawdown_percent?: number | null;
};

type BacktestResult = {
  method: Record<string, string>;
  universe: { symbols_requested: number; symbols_failed: number; selection: string };
  period: { from: string | null; to: string | null; observations: number };
  horizons: Record<string, { all: Stats; by_score_bucket: Record<string, Stats>; spearman_score_vs_return: number | null }>;
  bias_warnings: string[];
  duration_seconds: number;
};

type BacktestRun = { id: number | string; status: string; progress?: string; error?: string; result?: BacktestResult; finished_at?: string };

export default function BacktestPage() {
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchJson<BacktestRun>("/backtest/latest").then((payload) => setRun(payload.status === "empty" ? null : payload)).catch(() => undefined);
  }, []);

  async function start() {
    setRunning(true);
    setError("");
    try {
      const started = await postJson<BacktestRun>("/backtest/run?sample=150&step=5");
      const finished = await pollRun(`/backtest/runs/${started.id}`, (status) => setProgress(status.progress ?? status.status));
      if (finished.status !== "completed") throw new Error(finished.error ?? "backtest ไม่สำเร็จ");
      setRun(finished as BacktestRun);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "backtest ไม่สำเร็จ");
    } finally {
      setRunning(false);
    }
  }

  const result = run?.result;
  return (
    <main className="ambient relative mx-auto max-w-[1200px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-ink-subtle">Score Validation</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold text-ink"><FlaskConical className="h-6 w-6" /> Backtest คะแนน Opportunity</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            ตรวจว่าหุ้นที่ได้คะแนนสูง “หลังจากนั้น” ให้ผลตอบแทนดีกว่าจริงหรือไม่ — เพื่อไม่ให้ระบบให้คะแนนจากความรู้สึกอย่างเดียว
          </p>
        </div>
        <button onClick={() => void start()} disabled={running} className="focus-ring inline-flex h-10 items-center gap-2 rounded-xl bg-gain px-5 text-sm font-semibold text-bg-base disabled:opacity-40">
          {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {running ? progress || "กำลังรัน..." : "รัน Backtest ใหม่"}
        </button>
      </header>
      {error ? <p className="mb-4 rounded-xl border border-loss/30 bg-loss/10 p-3 text-sm text-loss">{error}</p> : null}
      {!result ? (
        <div className="panel p-10 text-center text-ink-subtle">ยังไม่มีผล backtest — กดรันเพื่อเริ่ม (ใช้เวลา 1-3 นาที)</div>
      ) : (
        <div className="space-y-4">
          <section className="panel border-amber/30 p-4">
            <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-amber"><AlertTriangle className="h-4 w-4" /> ข้อจำกัดที่ต้องอ่านก่อนดูตัวเลข</h2>
            <ul className="space-y-1 text-xs text-ink-muted">{result.bias_warnings.map((line) => <li key={line}>• {line}</li>)}</ul>
          </section>
          <section className="panel p-4 text-xs text-ink-muted">
            <div className="grid gap-1 sm:grid-cols-2">
              {Object.entries(result.method).map(([key, value]) => <div key={key}><b className="text-ink">{key}</b>: {value}</div>)}
              <div><b className="text-ink">period</b>: {result.period.from} → {result.period.to} ({result.period.observations.toLocaleString()} observations)</div>
              <div><b className="text-ink">universe</b>: {result.universe.symbols_requested} symbols ({result.universe.selection})</div>
              {run?.finished_at ? <div><b className="text-ink">run at</b>: {fmtTime(run.finished_at)}</div> : null}
            </div>
          </section>
          {Object.entries(result.horizons).map(([horizon, data]) => (
            <section key={horizon} className="panel overflow-x-auto p-4">
              <h2 className="mb-2 text-sm font-semibold text-ink">
                ถือ {horizon} · Spearman(score, return) = <span className="font-mono">{data.spearman_score_vs_return ?? "N/A"}</span>
                <span className="ml-2 text-[11px] font-normal text-ink-subtle">(ใกล้ 0 = คะแนนแทบไม่สัมพันธ์กับผลตอบแทน)</span>
              </h2>
              <table className="w-full min-w-[640px] text-xs">
                <thead className="text-left text-[10px] uppercase tracking-wider text-ink-subtle">
                  <tr><th className="py-1">Score</th><th className="text-right">n</th><th className="text-right">Win rate</th><th className="text-right">Avg</th><th className="text-right">Median</th><th className="text-right">Excess vs SPY</th><th className="text-right">Avg max DD</th><th className="text-right">Worst DD</th></tr>
                </thead>
                <tbody>
                  {[...Object.entries(data.by_score_bucket), ["ทั้งหมด", data.all] as [string, Stats]].map(([bucket, stats]) => (
                    <tr key={bucket} className="border-t border-bg-line/60 font-mono">
                      <td className="py-1.5 font-sans text-ink">{bucket}</td>
                      <td className="text-right">{stats.n}</td>
                      <td className="text-right">{fmtPct(stats.win_rate_percent, 1, false)}</td>
                      <td className="text-right">{fmtPct(stats.avg_return_percent)}</td>
                      <td className="text-right">{fmtPct(stats.median_return_percent)}</td>
                      <td className="text-right">{fmtPct(stats.avg_excess_vs_spy_percent)}</td>
                      <td className="text-right text-loss">{fmtPct(stats.avg_max_drawdown_percent)}</td>
                      <td className="text-right text-loss">{fmtPct(stats.worst_drawdown_percent)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
