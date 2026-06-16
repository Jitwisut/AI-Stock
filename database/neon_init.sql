-- AI Stock Radar - Neon PostgreSQL bootstrap
-- วิธีใช้: เปิด Neon Console > SQL Editor > วางไฟล์นี้ > Run
-- ไฟล์นี้ idempotent: รันซ้ำได้โดยไม่ลบข้อมูลเดิม

BEGIN;

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

CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks (symbol);
CREATE INDEX IF NOT EXISTS idx_stocks_active_exchange ON stocks (is_active, exchange);

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

CREATE INDEX IF NOT EXISTS idx_prices_symbol_timestamp
  ON prices (symbol, timestamp DESC);

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

CREATE INDEX IF NOT EXISTS idx_news_symbol_published_at
  ON news (symbol, published_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_indicators_symbol_date
  ON indicators (symbol, date DESC);

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

CREATE INDEX IF NOT EXISTS idx_scan_runs_status_finished_at
  ON scan_runs (status, finished_at DESC NULLS LAST, started_at DESC, id DESC);

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

CREATE INDEX IF NOT EXISTS idx_signals_created_at
  ON signals (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_created_at
  ON signals (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_scan_run_score
  ON signals (scan_run_id, score DESC);

CREATE INDEX IF NOT EXISTS idx_signals_scan_run_surge_score
  ON signals (scan_run_id, surge_score DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_signals_gap_percent
  ON signals (gap_percent DESC NULLS LAST);

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
  signal_id BIGINT REFERENCES signals(id) ON DELETE SET NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_alerts_signal_sent_at
  ON telegram_alerts (signal_id, sent_at DESC);

COMMIT;
