import { API_BASE_URL, delay, fetchJson } from "./api";

/* ─────────── Types: ตรงกับ workers/engine (ค่า null = ไม่มีข้อมูล → แสดง N/A) ─────────── */

export type SourcedField<T = number | string | null> = {
  value: T | null;
  source: string;
  as_of?: string | null;
  fetched_at?: string | null;
  note?: string | null;
};

export type Signal = {
  id: string;
  name: string;
  direction: "bullish" | "bearish";
  evidence: string[];
  values?: Record<string, unknown>;
};

export type ScoreBreakdown = Record<string, { points: number; weight: number; raw: number; raw_max: number }>;

export type ComponentRule = { rule: string; points: number; max: number; value: unknown };

export type Freshness = {
  source?: string;
  fetched_at?: string | null;
  market_timestamp?: string | null;
  last_bar_date?: string | null;
  expected_last_session?: string;
  sessions_behind?: number | null;
  is_stale: boolean;
  data_type?: string;
  is_realtime?: boolean;
  adjustment?: string;
  currency?: string;
};

export type DiscoveryResult = {
  rank: number;
  symbol: string;
  name: string;
  sector?: string | null;
  industry?: string | null;
  market_cap?: number | null;
  price: number;
  price_date: string;
  returns: Record<string, number | null>;
  opportunity_score: { total: number; breakdown: ScoreBreakdown };
  components: Record<string, { score: number; max: number }>;
  risk: { level: string; penalty_points: number; factors: string[] };
  confidence: { level: string; points: number; max_points: number };
  signals: Signal[];
  trend: { short_term: string; medium_term: string; long_term: string };
  metrics: Record<string, number | null>;
  fundamentals: Record<string, number | null> | null;
  news_count_72h: number | null;
  days_to_earnings: number | null;
  freshness: { last_bar_date: string; is_stale: boolean; sessions_behind: number | null };
};

export type DiscoveryPayload = {
  status?: string;
  market: string;
  strategy: { id: string; name: string; description: string; weights: Record<string, number>; filter: string };
  as_of_session: string;
  funnel: Record<string, number | string>;
  market_context: { condition: string | null; evidence: string[] | null };
  results: DiscoveryResult[];
  ranking_method: string;
  notes: string[];
  is_stale?: boolean;
  expected_last_session?: string;
  finished_at?: string;
  run_id?: number | string;
};

export type RunStatus = {
  id: number | string;
  status: "queued" | "running" | "completed" | "failed" | string;
  progress?: string;
  error?: string | null;
  result?: DiscoveryPayload | null;
};

export type StrategyInfo = { id: string; name: string; description: string; weights: Record<string, number>; filter: string };

export type MarketInfo = Record<string, { status: string; provider?: string; reason?: string }>;

export type StockAnalysis = {
  symbol: string;
  company_name: string | null;
  exchange: string | null;
  currency: string;
  strategy: { id: string; name: string; weights: Record<string, number> };
  quote: null | {
    price: SourcedField<number>;
    change: SourcedField<number>;
    change_percent: SourcedField<number>;
    volume: SourcedField<number>;
    market_status: string | null;
    is_realtime: boolean;
    delay_note: string;
    market_timestamp: string | null;
    fetched_at: string;
  };
  technical: Record<string, any>;
  data_quality: Record<string, any>;
  fundamentals: (Record<string, SourcedField<any>> & { _coverage?: { ratio: number; missing: string[] } }) | null;
  news: { items: NewsItem[]; status: string; errors: string[]; fetched_at: string; max_age_days: number };
  market_context: null | { condition: string; evidence: string[]; indices: Record<string, any>; vix: any; errors: string[] };
  sector_context: null | Record<string, any>;
  signals: Signal[];
  components: Record<string, { score: number; max: number; rules: ComponentRule[]; coverage?: number }>;
  opportunity_score: { total: number; max: number; breakdown: ScoreBreakdown };
  risk: {
    level: string;
    penalty_points: number;
    score: number;
    max: number;
    factors: { factor: string; value: unknown; penalty: number; explanation: string }[];
  };
  confidence: { level: string; points: number; max_points: number; factors: { factor: string; points: number; explanation: string }[] };
  multi_timeframe: {
    returns: Record<string, number | null>;
    short_term: { trend: string; evidence: string; horizon: string };
    medium_term: { trend: string; evidence: string; horizon: string };
    long_term: { trend: string; evidence: string; horizon: string };
    conflict: boolean;
    note: string | null;
  };
  key_levels: { name: string; price: number; type: string; distance_percent: number }[];
  days_to_earnings: number | null;
  data_freshness: {
    price_eod: Freshness;
    quote: { market_timestamp: string | null; fetched_at: string | null; is_realtime: boolean | null; delay_note: string | null; status: string };
    financials: { period_end: string | null; fetched_at: string | null; is_stale: boolean; status: string };
    news: { fetched_at: string; status: string };
    market_session: { status: string; label: string; now_market_time: string; last_completed_session: string };
  };
  unavailable: { part: string; provider: string; error: string }[];
  generated_at: string;
};

export type NewsItem = {
  title: string;
  url: string;
  source: string;
  published_at: string;
  age_hours: number;
  related_symbols: string[];
  provider: string;
};

export type AiExplanation = {
  status: "ok" | "unavailable";
  reason?: string;
  model?: string;
  requested_model?: string;
  generated_at?: string;
  cache?: string;
  input_hash?: string;
  role?: string;
  disclaimer?: string;
  validation?: { removed_count: number; removed_items: { field: string; reason: string; text: string }[] };
  output?: {
    summary?: string;
    why_interesting?: { point: string; evidence: string }[];
    signal_explanations?: { signal_id: string; explanation: string }[];
    risk_explanations?: { risk: string; explanation: string }[];
    bull_case?: string[];
    bear_case?: string[];
    scenarios_to_watch?: { scenario: string; trigger: string }[];
    news_assessment?: {
      fact: { title: string; source: string; published_at: string; age_hours: number };
      ai_classification: string;
      ai_interpretation: string;
    }[];
    sector_vs_company?: string;
    insufficient_data?: string[];
    overall_view?: string;
  };
};

/* ─────────── Requests ─────────── */

/** POST ที่ไม่ต้องรอผลยาว — เซิร์ฟเวอร์ตอบสถานะงานทันที */
export async function postJson<T>(path: string): Promise<T> {
  let lastError: Error = new Error("เรียก API ไม่สำเร็จ");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method: "POST",
        signal: AbortSignal.timeout(60_000)
      });
      if (response.status >= 500 && attempt < 2) {
        await delay(2000 * (attempt + 1));
        continue;
      }
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        // 4xx = คำขอผิด/ใช้ไม่ได้ (เช่นตลาด SET ยังไม่รองรับ) — ลองซ้ำก็ไม่หาย แจ้งทันที
        const clientError = new Error((body as { detail?: string }).detail ?? `เรียก API ไม่สำเร็จ (${response.status})`);
        clientError.name = "ClientError";
        throw clientError;
      }
      return body as T;
    } catch (error) {
      lastError = error instanceof Error ? error : lastError;
      if (attempt === 2 || lastError.name === "ClientError") throw lastError;
      await delay(2000 * (attempt + 1));
    }
  }
  throw lastError;
}

/** GET ที่คืนรายละเอียด 503 ของ provider (Data temporarily unavailable) แทนการโยน error เปล่า ๆ */
export async function getWithStatus<T>(path: string, timeoutMs = 90_000): Promise<{ data?: T; unavailable?: UnavailableInfo; error?: string }> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.json().catch(() => ({}));
    if (response.status === 503 && (body as UnavailableInfo).provider) {
      return { unavailable: body as UnavailableInfo };
    }
    if (!response.ok) {
      return { error: (body as { detail?: string }).detail ?? `เรียก API ไม่สำเร็จ (${response.status})` };
    }
    return { data: body as T };
  } catch (error) {
    return { error: error instanceof Error && error.name !== "TimeoutError" ? error.message : "เซิร์ฟเวอร์ไม่ตอบสนอง ลองใหม่อีกครั้ง" };
  }
}

export type UnavailableInfo = {
  detail: string;
  provider: string;
  error: string;
  last_successful_update: string | null;
  retry_status: string;
  retry_after_seconds: number;
};

export async function pollRun(
  path: string,
  onProgress: (status: RunStatus) => void,
  { intervalMs = 3000, maxMs = 15 * 60_000 } = {}
): Promise<RunStatus> {
  const started = Date.now();
  let status: RunStatus = await fetchJson<RunStatus>(path, { retries: 1, timeoutMs: 20_000 });
  while (status.status === "queued" || status.status === "running") {
    onProgress(status);
    if (Date.now() - started > maxMs) throw new Error("งานใช้เวลานานผิดปกติ ลองโหลดใหม่ภายหลัง");
    await delay(intervalMs);
    try {
      status = await fetchJson<RunStatus>(path, { retries: 1, timeoutMs: 20_000 });
    } catch {
      // พลาดรอบเดียวไม่เป็นไร
    }
  }
  return status;
}

/* ─────────── Formatting: null → "N/A" เสมอ ห้ามแสดง 0 แทนค่าที่ไม่มี ─────────── */

export const NA = "N/A";

export function fmtNum(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : NA;
}

export function fmtPct(value: unknown, digits = 2, signed = true): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NA;
  return `${signed && value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function fmtMoney(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${fmtNum(value, digits)}` : NA;
}

export function fmtBig(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NA;
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function fmtVolume(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NA;
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

/** เวลาแบบระบุโซนชัดเจน — ผู้ใช้ต้องรู้ว่าเป็นเวลาตลาด (ET) หรือเวลาไทย */
export function fmtTime(iso: string | null | undefined, zone: "th" | "et" = "th"): string {
  if (!iso) return NA;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const tz = zone === "et" ? "America/New_York" : "Asia/Bangkok";
  const label = zone === "et" ? "ET" : "เวลาไทย";
  return `${date.toLocaleString("th-TH-u-ca-gregory", { timeZone: tz, day: "numeric", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" })} ${label}`;
}

export function fmtAge(hours: number | null | undefined): string {
  if (typeof hours !== "number") return NA;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} นาทีที่แล้ว`;
  if (hours < 48) return `${Math.round(hours)} ชม.ที่แล้ว`;
  return `${Math.round(hours / 24)} วันที่แล้ว`;
}

export const COMPONENT_LABEL: Record<string, string> = {
  trend: "Trend",
  momentum: "Momentum",
  volume: "Volume",
  fundamental: "Fundamental",
  risk: "Risk",
  catalyst: "Catalyst"
};

export const RISK_TONE: Record<string, string> = {
  Low: "border-gain/30 bg-gain/10 text-gain",
  Medium: "border-amber/30 bg-amber/10 text-amber",
  High: "border-loss/30 bg-loss/10 text-loss",
  "Very High": "border-loss/50 bg-loss/20 text-loss"
};

export const CONFIDENCE_TONE: Record<string, string> = {
  High: "border-gain/30 bg-gain/10 text-gain",
  Medium: "border-amber/30 bg-amber/10 text-amber",
  Low: "border-bg-line bg-bg-raised text-ink-muted"
};

export const TREND_TONE: Record<string, string> = {
  Up: "text-gain",
  Down: "text-loss",
  Sideways: "text-amber",
  "Insufficient data": "text-ink-subtle"
};

export const CONDITION_TONE: Record<string, string> = {
  Bullish: "border-gain/30 bg-gain/10 text-gain",
  Neutral: "border-amber/30 bg-amber/10 text-amber",
  "Risk-Off": "border-loss/30 bg-loss/10 text-loss",
  Unknown: "border-bg-line bg-bg-raised text-ink-muted"
};
