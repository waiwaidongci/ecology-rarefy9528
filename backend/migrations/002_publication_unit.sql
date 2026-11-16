-- Publication unit: attempts, archive evidence and publication identity.
-- Existing metadata.create_all databases gain the columns via ALTER; fresh
-- databases get them from the models directly. Kept idempotent on purpose
-- because there is no production migration runner in this demo stack.
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS request_key VARCHAR(64);
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS attempt_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS current_attempt_id INTEGER;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS published_attempt_id INTEGER;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS status_since TIMESTAMPTZ;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS artifact_path VARCHAR(300);
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS artifact_sha256 VARCHAR(64);
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS artifact_bytes INTEGER;
ALTER TABLE diversity_runs ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS ux_diversity_runs_request_key ON diversity_runs(request_key);

CREATE TABLE IF NOT EXISTS run_attempts (
  id SERIAL PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES diversity_runs(id),
  attempt_no INTEGER,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  token VARCHAR(64) NOT NULL,
  worker VARCHAR(120),
  reason VARCHAR(500),
  recovered_from_attempt_id INTEGER,
  artifact_path VARCHAR(300),
  artifact_sha256 VARCHAR(64),
  artifact_bytes INTEGER,
  started_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ux_run_attempts_run_no UNIQUE (run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_run_attempts_run_id ON run_attempts(run_id);
