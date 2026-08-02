"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Info,
  LineChart,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { delay, fetchJsonWithStatus } from "../../lib/api";

type LongTermBreakdown = {
  trend: number;
  momentum: number;
  position: number;
  stability: number;
  size: number;
};

type LongTermPick = {
  symbol: string;
  name: string;
  sector?: string | null;
  price: number;
  score: number;
  grade: "A" | "B" | "C" | string;
  label: string;
  tone: "strong" | "watch" | "caution" | string;
  breakdown: LongTermBreakdown;
  reasons: string[];
  risks: string[];
  sma200: number;
  above_sma200: boolean;
  week52_high: number;
  week52_low: number;
  pct_from_52w_high: number;
  return_3m_percent: number;
  return_6m_percent: number;
  volatility_percent: number;
};

const TONE: Record<string, { text: string; ring: string; bg: string }> = {
  strong: { text: "text-gain", ring: "ring-gain/40", bg: "bg-gain/10" },
  watch: { text: "text-amber", ring: "ring-amber/40", bg: "bg-amber/10" },
  caution: { text: "text-ink-muted", ring: "ring-bg-line", bg: "bg-bg-raised" }
};

const BREAKDOWN_MAX: LongTermBreakdown = {
  trend: 35,
  momentum: 20,
  position: 20,
  stability: 15,
  size: 10
};

const BREAKDOWN_LABEL: Record<keyof LongTermBreakdown, string> = {
  trend: "เทรนด์ (เหนือ MA200)",
  momentum: "โมเมนตัม 6 เดือน",
  position: "ตำแหน่งใน 52 สัปดาห์",
  stability: "เสถียรภาพ (ผันผวนต่ำ)",
  size: "ขนาดบริษัท"
};

export default function LongTermPage() {
  const [picks, setPicks] = useState<LongTermPick[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mobilePickOpen, setMobilePickOpen] = useState(false);

  async function load() {
    setError("");
    setLoading(true);
    try {
      // ตอบชุดที่มีทันที แล้วถามซ้ำระหว่างที่เซิร์ฟเวอร์คำนวณชุดใหม่เบื้องหลัง
      for (let attempt = 0; attempt < 24; attempt++) {
        const { data, refreshing } = await fetchJsonWithStatus<LongTermPick[]>(
          "/signals/longterm?limit=30"
        );
        if (data.length > 0) {
          setPicks(data);
          setSelected((current) => current || data[0]?.symbol || "");
        }
        if (!refreshing) break;
        await delay(5_000);
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const active = useMemo(
    () => picks.find((pick) => pick.symbol === selected) ?? picks[0],
    [picks, selected]
  );

  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-subtle">
            ระยะยาว
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            หุ้นน่าสะสมระยะยาว
          </h1>
          <p className="mt-1.5 max-w-xl text-sm leading-6 text-ink-muted">
            จัดอันดับเชิงเทรนด์ (MA200, โมเมนตัม 3-6 เดือน, ตำแหน่ง 52 สัปดาห์, ความผันผวน)
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 self-start rounded-full border border-bg-line bg-bg-surface px-4 text-sm font-medium text-ink-muted transition-colors duration-200 hover:text-ink disabled:opacity-60 lg:self-auto"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          รีเฟรช
        </button>
      </header>

      <div className="mb-5 flex items-start gap-3 rounded-xl border border-accent-cyan/30 bg-accent-cyan/10 px-4 py-3 text-sm text-accent-cyan">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p className="leading-6">
          วิเคราะห์จาก <strong>เทรนด์ราคาเท่านั้น</strong> (ยังไม่รวมปัจจัยพื้นฐาน เช่น กำไร/รายได้)
          คัดจากหุ้นคุณภาพขนาดใหญ่ เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำการลงทุน
        </p>
      </div>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      <section className="grid gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* Table */}
        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left text-sm">
              <thead className="bg-bg-base/40 text-[11px] uppercase tracking-wider text-ink-subtle">
                <tr>
                  <th className="px-4 py-3 font-medium">#</th>
                  <th className="px-4 py-3 font-medium">Symbol</th>
                  <th className="px-4 py-3 font-medium">คะแนน</th>
                  <th className="px-4 py-3 font-medium">เกรด</th>
                  <th className="px-4 py-3 font-medium">6 เดือน</th>
                  <th className="px-4 py-3 font-medium">MA200</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-ink-subtle">
                      กำลังวิเคราะห์...
                    </td>
                  </tr>
                ) : picks.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-ink-subtle">
                      ยังไม่มีข้อมูล
                    </td>
                  </tr>
                ) : (
                  picks.map((pick, index) => {
                    const tone = TONE[pick.tone] ?? TONE.caution;
                    const isSelected = active?.symbol === pick.symbol;
                    return (
                      <tr
                        key={pick.symbol}
                        onClick={() => { setSelected(pick.symbol); setMobilePickOpen(true); }}
                        className={`cursor-pointer border-t border-bg-line transition ${
                          isSelected ? "bg-gain/[0.06]" : "hover:bg-bg-raised/50"
                        }`}
                      >
                        <td className="px-4 py-3 text-xs text-ink-subtle">{index + 1}</td>
                        <td className="px-4 py-3">
                          <div className="font-mono text-sm font-semibold text-ink">{pick.symbol}</div>
                          <div className="max-w-[180px] truncate text-[11px] text-ink-subtle">
                            {pick.name}
                          </div>
                        </td>
                        <td className="px-4 py-3 font-mono font-semibold text-ink">
                          {pick.score.toFixed(0)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex h-7 min-w-7 items-center justify-center rounded-lg px-2 text-xs font-bold ring-1 ${tone.ring} ${tone.text} ${tone.bg}`}
                          >
                            {pick.grade}
                          </span>
                        </td>
                        <td
                          className={`px-4 py-3 font-mono text-xs ${
                            pick.return_6m_percent >= 0 ? "text-gain" : "text-loss"
                          }`}
                        >
                          {pick.return_6m_percent >= 0 ? "+" : ""}
                          {pick.return_6m_percent.toFixed(0)}%
                        </td>
                        <td className="px-4 py-3">
                          {pick.above_sma200 ? (
                            <span className="chip border-gain/30 bg-gain/10 text-gain">
                              <TrendingUp className="h-3 w-3" /> เหนือ
                            </span>
                          ) : (
                            <span className="chip text-ink-subtle">ต่ำกว่า</span>
                          )}
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
        <aside className="panel hidden p-4 lg:block lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
          {active ? (
            <LongTermDetail pick={active} />
          ) : (
            <div className="flex min-h-[300px] items-center justify-center text-sm text-ink-subtle">
              {loading ? "กำลังโหลด..." : "เลือกหุ้นเพื่อดูรายละเอียด"}
            </div>
          )}
        </aside>
      </section>

      <footer className="mt-6 text-center text-[11px] text-ink-subtle">
        วิเคราะห์เชิงเทรนด์จากราคาย้อนหลัง 1 ปี · ไม่ใช่คำแนะนำการลงทุน
      </footer>

      <MobilePickDrawer
        open={mobilePickOpen}
        onClose={() => setMobilePickOpen(false)}
        pick={active}
      />
    </main>
  );
}

function LongTermDetail({ pick }: { pick: LongTermPick }) {
  const tone = TONE[pick.tone] ?? TONE.caution;
  const breakdown = Object.entries(pick.breakdown) as [keyof LongTermBreakdown, number][];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-mono text-2xl font-semibold text-ink">{pick.symbol}</h2>
          <div className="text-xs text-ink-muted">{pick.name}</div>
          {pick.sector ? <div className="text-[11px] text-ink-subtle">{pick.sector}</div> : null}
        </div>
        <div className="text-right">
          <div className={`text-3xl font-bold ${tone.text}`}>{pick.score.toFixed(0)}</div>
          <div className={`text-xs font-semibold ${tone.text}`}>{pick.label}</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="ราคา" value={`$${pick.price.toFixed(2)}`} />
        <Metric label="MA200" value={`$${pick.sma200.toFixed(2)}`} />
        <Metric
          label="ห่างจุดสูง 52 สัปดาห์"
          value={`${pick.pct_from_52w_high.toFixed(1)}%`}
          tone={pick.pct_from_52w_high >= -10 ? "text-gain" : "text-ink"}
        />
        <Metric label="ผันผวน/ปี" value={`${pick.volatility_percent.toFixed(0)}%`} />
        <Metric
          label="3 เดือน"
          value={`${pick.return_3m_percent >= 0 ? "+" : ""}${pick.return_3m_percent.toFixed(0)}%`}
          tone={pick.return_3m_percent >= 0 ? "text-gain" : "text-loss"}
        />
        <Metric
          label="6 เดือน"
          value={`${pick.return_6m_percent >= 0 ? "+" : ""}${pick.return_6m_percent.toFixed(0)}%`}
          tone={pick.return_6m_percent >= 0 ? "text-gain" : "text-loss"}
        />
      </div>

      <section>
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
          รายละเอียดคะแนน
        </h3>
        <div className="mt-3 flex flex-col gap-2.5">
          {breakdown.map(([key, value]) => {
            const max = BREAKDOWN_MAX[key] ?? 100;
            return (
              <div key={key}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="text-ink-muted">{BREAKDOWN_LABEL[key]}</span>
                  <span className="font-mono font-medium text-ink">
                    {value.toFixed(0)} <span className="text-ink-subtle">/{max}</span>
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-bg-base">
                  <div
                    className="h-full rounded-full bg-gain"
                    style={{ width: `${Math.min(100, (value / max) * 100)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {pick.reasons.length > 0 ? (
        <section>
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-gain">
            <CheckCircle2 className="h-3 w-3" /> เหตุผลที่น่าสนใจ
          </h3>
          <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink">
            {pick.reasons.map((reason) => (
              <li key={reason} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gain" />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {pick.risks.length > 0 ? (
        <section>
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-loss">
            <ShieldAlert className="h-3 w-3" /> ข้อควรระวัง
          </h3>
          <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink-muted">
            {pick.risks.map((risk) => (
              <li key={risk} className="flex gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-loss/80" />
                <span>{risk}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Metric({ label, value, tone = "text-ink" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-bg-line bg-bg-base/40 px-2.5 py-2">
      <div className="text-[10px] text-ink-subtle">{label}</div>
      <div className={`stat-number text-sm font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}

function MobilePickDrawer({
  open,
  onClose,
  pick
}: {
  open: boolean;
  onClose: () => void;
  pick: LongTermPick | undefined;
}) {
  if (!pick) return null;
  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity lg:hidden ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />
      <div
        className={`fixed bottom-0 left-0 right-0 z-50 max-h-[88dvh] overflow-y-auto rounded-t-2xl border-t border-bg-line bg-bg-surface p-4 transition-transform duration-300 ease-out lg:hidden ${
          open ? "translate-y-0" : "translate-y-full"
        }`}
      >
        <div className="relative mb-4 flex items-center justify-center">
          <div className="h-1 w-10 rounded-full bg-bg-line" />
          <button
            type="button"
            onClick={onClose}
            className="absolute right-0 rounded-lg border border-bg-line p-1.5 text-ink-subtle hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <LongTermDetail pick={pick} />
        <div className="h-4" />
      </div>
    </>
  );
}
