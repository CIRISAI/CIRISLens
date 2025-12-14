-- Status checks table for tracking service and provider availability
-- Used by CIRISLens to aggregate status from all CIRIS services

-- Create status_checks table
CREATE TABLE IF NOT EXISTS cirislens.status_checks (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    service_name VARCHAR(50) NOT NULL,      -- 'cirisbilling', 'cirisproxy', 'cirislens'
    provider_name VARCHAR(50) NOT NULL,     -- 'postgresql', 'openrouter', 'grafana', etc.
    status VARCHAR(20) NOT NULL,            -- 'operational', 'degraded', 'outage'
    latency_ms INTEGER,
    error_message TEXT,
    PRIMARY KEY (id, timestamp)
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('cirislens.status_checks', 'timestamp',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_status_checks_service
    ON cirislens.status_checks (service_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_status_checks_provider
    ON cirislens.status_checks (provider_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_status_checks_status
    ON cirislens.status_checks (status, timestamp DESC);

-- Retention: 90 days of detailed data
SELECT add_retention_policy('cirislens.status_checks',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

-- Compression after 7 days
ALTER TABLE cirislens.status_checks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'service_name, provider_name',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('cirislens.status_checks',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Hourly availability continuous aggregate
CREATE MATERIALIZED VIEW IF NOT EXISTS cirislens.status_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp) AS hour,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / NULLIF(COUNT(*), 0) AS uptime_pct,
    AVG(latency_ms)::INTEGER AS avg_latency_ms,
    MAX(latency_ms) AS max_latency_ms,
    COUNT(*) AS check_count,
    COUNT(*) FILTER (WHERE status = 'outage') AS outage_count
FROM cirislens.status_checks
GROUP BY hour, service_name, provider_name
WITH NO DATA;

-- Refresh policy for hourly aggregate
SELECT add_continuous_aggregate_policy('cirislens.status_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Daily availability continuous aggregate
CREATE MATERIALIZED VIEW IF NOT EXISTS cirislens.status_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', timestamp) AS day,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / NULLIF(COUNT(*), 0) AS uptime_pct,
    AVG(latency_ms)::INTEGER AS avg_latency_ms,
    COUNT(*) AS check_count,
    COUNT(*) FILTER (WHERE status = 'outage') AS outage_count
FROM cirislens.status_checks
GROUP BY day, service_name, provider_name
WITH NO DATA;

-- Refresh policy for daily aggregate
SELECT add_continuous_aggregate_policy('cirislens.status_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Retention for daily aggregate (1 year)
SELECT add_retention_policy('cirislens.status_daily',
    INTERVAL '365 days',
    if_not_exists => TRUE
);

-- Latest status cache table (for fast lookups)
CREATE TABLE IF NOT EXISTS cirislens.status_latest (
    service_name VARCHAR(50) NOT NULL,
    provider_name VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    latency_ms INTEGER,
    error_message TEXT,
    last_check TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (service_name, provider_name)
);

-- Function to update latest status
CREATE OR REPLACE FUNCTION cirislens.update_status_latest()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO cirislens.status_latest (service_name, provider_name, status, latency_ms, error_message, last_check)
    VALUES (NEW.service_name, NEW.provider_name, NEW.status, NEW.latency_ms, NEW.error_message, NEW.timestamp)
    ON CONFLICT (service_name, provider_name) DO UPDATE SET
        status = EXCLUDED.status,
        latency_ms = EXCLUDED.latency_ms,
        error_message = EXCLUDED.error_message,
        last_check = EXCLUDED.last_check;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update latest status
DROP TRIGGER IF EXISTS status_checks_update_latest ON cirislens.status_checks;
CREATE TRIGGER status_checks_update_latest
    AFTER INSERT ON cirislens.status_checks
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_status_latest();
