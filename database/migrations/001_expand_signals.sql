ALTER TABLE signals ADD COLUMN IF NOT EXISTS gap_percent NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS relative_volume NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS latest_price NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS previous_close NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS day_high NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS prior_high NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_cap NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS surge_score NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS surge_grade JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS surge_plan JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS recommendation JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS provider_updated_at TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS data_source TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS catalyst TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS risks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS watch_plan JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS news JSONB NOT NULL DEFAULT '[]'::jsonb;

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

ALTER TABLE signals ADD COLUMN IF NOT EXISTS scan_run_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_signals_scan_run_score ON signals (scan_run_id, score DESC);
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
