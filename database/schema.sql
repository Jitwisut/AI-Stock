CREATE TABLE IF NOT EXISTS stocks (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL UNIQUE,
  name TEXT,
  exchange TEXT,
  sector TEXT,
  market_cap NUMERIC,
  float_shares NUMERIC,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS prices (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  volume BIGINT,
  premarket_price NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_timestamp ON prices (symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS news (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  source TEXT,
  published_at TIMESTAMPTZ,
  summary TEXT,
  sentiment_score NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_news_symbol_published_at ON news (symbol, published_at DESC);

CREATE TABLE IF NOT EXISTS indicators (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  date DATE NOT NULL,
  ema_20 NUMERIC,
  ema_50 NUMERIC,
  rsi NUMERIC,
  relative_volume NUMERIC,
  gap_percent NUMERIC,
  breakout_level NUMERIC,
  UNIQUE (symbol, date)
);

CREATE TABLE IF NOT EXISTS signals (
  id BIGSERIAL PRIMARY KEY,
  scan_run_id BIGINT,
  symbol TEXT NOT NULL,
  score NUMERIC NOT NULL,
  momentum_score NUMERIC NOT NULL,
  volume_score NUMERIC NOT NULL,
  catalyst_score NUMERIC NOT NULL,
  premarket_gap_score NUMERIC NOT NULL,
  breakout_score NUMERIC NOT NULL,
  risk_score NUMERIC NOT NULL,
  gap_percent NUMERIC,
  relative_volume NUMERIC,
  latest_price NUMERIC,
  previous_close NUMERIC,
  day_high NUMERIC,
  prior_high NUMERIC,
  market_cap NUMERIC,
  surge_score NUMERIC,
  surge_grade JSONB NOT NULL DEFAULT '{}'::jsonb,
  surge_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
  recommendation JSONB NOT NULL DEFAULT '{}'::jsonb,
  provider_updated_at TIMESTAMPTZ,
  data_source TEXT,
  catalyst TEXT,
  reason TEXT,
  risks JSONB NOT NULL DEFAULT '[]'::jsonb,
  watch_plan JSONB NOT NULL DEFAULT '[]'::jsonb,
  news JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_scan_run_score ON signals (scan_run_id, score DESC);

CREATE TABLE IF NOT EXISTS scan_runs (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  universe_count INTEGER NOT NULL DEFAULT 0,
  scanned_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_kind_status_started_at
  ON scan_runs (kind, status, started_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_signals_scan_run'
  ) THEN
    ALTER TABLE signals
      ADD CONSTRAINT fk_signals_scan_run
      FOREIGN KEY (scan_run_id)
      REFERENCES scan_runs(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS telegram_alerts (
  id BIGSERIAL PRIMARY KEY,
  signal_id BIGINT REFERENCES signals(id),
  sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL
);

-- ── Telegram bot: watchlist / ผู้รับ digest / พอร์ตจำลอง ────────────────────
-- ผูกกับ chat_id ของ Telegram โดยตรง (ยังไม่มีระบบ user ของเว็บ)

CREATE TABLE IF NOT EXISTS bot_watchlist (
  id BIGSERIAL PRIMARY KEY,
  chat_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  target_price NUMERIC,
  direction TEXT NOT NULL DEFAULT 'above',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_alert_at TIMESTAMPTZ,
  UNIQUE (chat_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_bot_watchlist_symbol ON bot_watchlist (symbol);

CREATE TABLE IF NOT EXISTS bot_subscribers (
  chat_id TEXT PRIMARY KEY,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_portfolio (
  id BIGSERIAL PRIMARY KEY,
  chat_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  quantity NUMERIC NOT NULL,
  cost_basis NUMERIC NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (chat_id, symbol)
);

-- ---------------------------------------------------------------------------
-- Discovery engine (workers/engine) — ผลสแกนแบบ snapshot + แคชแท่งราคารายวัน
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_runs (
    id BIGSERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    as_of_session DATE,
    funnel JSONB,
    market_context JSONB,
    results JSONB,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_lookup
    ON discovery_runs (market, strategy, started_at DESC);

-- แท่งราคารายวันเปลี่ยนวันละครั้ง เก็บไว้กันต้องดึงใหม่ทั้งชุดทุกครั้งที่เซิร์ฟเวอร์ตื่น
CREATE TABLE IF NOT EXISTS bar_cache (
    symbol TEXT PRIMARY KEY,
    last_bar_date DATE NOT NULL,
    bars JSONB NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    results JSONB,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- คำอธิบายจาก AI แคชตาม snapshot ของข้อมูล (hash ของ input) — ข้อมูลเดิม = ไม่เรียก LLM ซ้ำ
CREATE TABLE IF NOT EXISTS ai_analysis_cache (
    input_hash TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    model TEXT,
    output JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ผลคัด "หุ้นน่าสนใจวันนี้" ทุกรอบ (ทั้งตัวที่ผ่านด่านแรก ไม่ใช่แค่ตัวที่แสดง) ไว้วัดผลย้อนหลัง
-- ว่าคะแนน AI ดีกว่าคะแนนกฎไหม · หนึ่งแถวต่อ (วันทำการ, หุ้น) เก็บรอบที่ "ดีที่สุด" ของวัน
-- (quality: สร้างตอนตลาดปิด +2, มีคะแนน AI +1) · market_open = สร้างระหว่างตลาดเปิด ไม่ใช้วัดผล
-- คะแนน AI ของวันทำการเดียวกันถูกใช้ซ้ำ (ข้อมูล EOD ไม่เปลี่ยนจนปิดตลาดรอบถัดไป) → AI ~1 ครั้ง/วัน
CREATE TABLE IF NOT EXISTS pick_snapshots (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_open BOOLEAN NOT NULL DEFAULT FALSE,
    quality SMALLINT NOT NULL DEFAULT 0,
    prescreen_rank INTEGER,
    in_shortlist BOOLEAN NOT NULL DEFAULT FALSE,
    rule_score NUMERIC,
    ai_score NUMERIC,
    final_score NUMERIC,
    final_rank INTEGER,
    ai_model TEXT,
    score_breakdown JSONB,
    -- reason / catalyst / risk ที่ AI เขียน — ใช้ซ้ำได้ทั้งวันทำการ ไม่ต้องเรียก AI ใหม่
    ai_notes JSONB,
    UNIQUE (session_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_pick_snapshots_session ON pick_snapshots (session_date);
