-- Add region support to status_checks for multi-region monitoring
-- Regions: 'us', 'eu', 'global' (for providers not region-specific)

-- Add region column to status_checks
ALTER TABLE cirislens.status_checks
ADD COLUMN IF NOT EXISTS region VARCHAR(10) NOT NULL DEFAULT 'global';

-- Create index for region queries
CREATE INDEX IF NOT EXISTS idx_status_checks_region
    ON cirislens.status_checks (region, timestamp DESC);

-- Add region to status_latest
ALTER TABLE cirislens.status_latest
ADD COLUMN IF NOT EXISTS region VARCHAR(10) NOT NULL DEFAULT 'global';

-- Update primary key to include region
ALTER TABLE cirislens.status_latest DROP CONSTRAINT IF EXISTS status_latest_pkey;
ALTER TABLE cirislens.status_latest
ADD PRIMARY KEY (service_name, provider_name, region);

-- Update the trigger function to include region
CREATE OR REPLACE FUNCTION cirislens.update_status_latest()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO cirislens.status_latest (service_name, provider_name, region, status, latency_ms, error_message, last_check)
    VALUES (NEW.service_name, NEW.provider_name, NEW.region, NEW.status, NEW.latency_ms, NEW.error_message, NEW.timestamp)
    ON CONFLICT (service_name, provider_name, region) DO UPDATE SET
        status = EXCLUDED.status,
        latency_ms = EXCLUDED.latency_ms,
        error_message = EXCLUDED.error_message,
        last_check = EXCLUDED.last_check;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop and recreate continuous aggregates with region support
-- Note: This will lose historical aggregate data (raw data preserved in status_checks)

DROP MATERIALIZED VIEW IF EXISTS cirislens.status_daily CASCADE;
DROP MATERIALIZED VIEW IF EXISTS cirislens.status_hourly CASCADE;

-- Recreate hourly aggregate with region
CREATE MATERIALIZED VIEW cirislens.status_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp) AS hour,
    region,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / NULLIF(COUNT(*), 0) AS uptime_pct,
    AVG(latency_ms)::INTEGER AS avg_latency_ms,
    MAX(latency_ms) AS max_latency_ms,
    COUNT(*) AS check_count,
    COUNT(*) FILTER (WHERE status = 'outage') AS outage_count
FROM cirislens.status_checks
GROUP BY hour, region, service_name, provider_name
WITH NO DATA;

SELECT add_continuous_aggregate_policy('cirislens.status_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Recreate daily aggregate with region
CREATE MATERIALIZED VIEW cirislens.status_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', timestamp) AS day,
    region,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / NULLIF(COUNT(*), 0) AS uptime_pct,
    AVG(latency_ms)::INTEGER AS avg_latency_ms,
    COUNT(*) AS check_count,
    COUNT(*) FILTER (WHERE status = 'outage') AS outage_count
FROM cirislens.status_checks
GROUP BY day, region, service_name, provider_name
WITH NO DATA;

SELECT add_continuous_aggregate_policy('cirislens.status_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

SELECT add_retention_policy('cirislens.status_daily',
    INTERVAL '365 days',
    if_not_exists => TRUE
);

-- Update compression settings to include region
ALTER TABLE cirislens.status_checks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'region, service_name, provider_name',
    timescaledb.compress_orderby = 'timestamp DESC'
);
