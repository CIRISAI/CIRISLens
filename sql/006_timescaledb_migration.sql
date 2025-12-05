-- TimescaleDB Migration for CIRISLens
-- Converts existing tables to hypertables with automatic compression and retention

-- Step 1: Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Step 2: Convert agent_metrics to hypertable
-- Note: This preserves existing data
DO $$
BEGIN
    -- Check if already a hypertable
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'agent_metrics'
    ) THEN
        -- Drop the existing primary key (hypertables need time column in PK)
        ALTER TABLE cirislens.agent_metrics DROP CONSTRAINT IF EXISTS agent_metrics_pkey;

        -- Create hypertable with automatic chunking (7 day intervals)
        PERFORM create_hypertable('cirislens.agent_metrics', 'timestamp',
            chunk_time_interval => INTERVAL '7 days',
            migrate_data => true,
            if_not_exists => true
        );

        -- Recreate unique constraint including time column
        CREATE UNIQUE INDEX IF NOT EXISTS agent_metrics_unique_idx
        ON cirislens.agent_metrics(agent_name, metric_name, timestamp, labels);

        RAISE NOTICE 'Converted agent_metrics to hypertable';
    ELSE
        RAISE NOTICE 'agent_metrics is already a hypertable';
    END IF;
END $$;

-- Step 3: Convert agent_logs to hypertable
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'agent_logs'
    ) THEN
        PERFORM create_hypertable('cirislens.agent_logs', 'timestamp',
            chunk_time_interval => INTERVAL '1 day',
            migrate_data => true,
            if_not_exists => true
        );
        RAISE NOTICE 'Converted agent_logs to hypertable';
    ELSE
        RAISE NOTICE 'agent_logs is already a hypertable';
    END IF;
END $$;

-- Step 4: Convert agent_traces to hypertable
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'agent_traces'
    ) THEN
        PERFORM create_hypertable('cirislens.agent_traces', 'start_time',
            chunk_time_interval => INTERVAL '1 day',
            migrate_data => true,
            if_not_exists => true
        );
        RAISE NOTICE 'Converted agent_traces to hypertable';
    ELSE
        RAISE NOTICE 'agent_traces is already a hypertable';
    END IF;
END $$;

-- Step 5: Enable compression on hypertables
-- Compression settings: segment by agent_name, order by timestamp DESC
ALTER TABLE cirislens.agent_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'agent_name,metric_name',
    timescaledb.compress_orderby = 'timestamp DESC'
);

ALTER TABLE cirislens.agent_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'agent_name,severity',
    timescaledb.compress_orderby = 'timestamp DESC'
);

ALTER TABLE cirislens.agent_traces SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'agent_name',
    timescaledb.compress_orderby = 'start_time DESC'
);

-- Step 6: Add compression policies (compress data older than 7 days)
-- These run automatically in the background
SELECT add_compression_policy('cirislens.agent_metrics', INTERVAL '7 days', if_not_exists => true);
SELECT add_compression_policy('cirislens.agent_logs', INTERVAL '7 days', if_not_exists => true);
SELECT add_compression_policy('cirislens.agent_traces', INTERVAL '7 days', if_not_exists => true);

-- Step 7: Add retention policies (automatically drop data older than retention period)
-- Metrics: 30 days retention
-- Logs: 14 days retention
-- Traces: 14 days retention
SELECT add_retention_policy('cirislens.agent_metrics', INTERVAL '30 days', if_not_exists => true);
SELECT add_retention_policy('cirislens.agent_logs', INTERVAL '14 days', if_not_exists => true);
SELECT add_retention_policy('cirislens.agent_traces', INTERVAL '14 days', if_not_exists => true);

-- Step 8: Create continuous aggregate for hourly metric summaries
-- This creates pre-computed rollups that survive data retention
CREATE MATERIALIZED VIEW IF NOT EXISTS cirislens.metrics_hourly
WITH (timescaledb.continuous) AS
SELECT
    agent_name,
    metric_name,
    time_bucket('1 hour', timestamp) AS bucket,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    count(*) AS sample_count
FROM cirislens.agent_metrics
GROUP BY agent_name, metric_name, time_bucket('1 hour', timestamp)
WITH NO DATA;

-- Add refresh policy for continuous aggregate (refresh every hour, keep 90 days of hourly data)
SELECT add_continuous_aggregate_policy('cirislens.metrics_hourly',
    start_offset => INTERVAL '90 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => true
);

-- Step 9: Create continuous aggregate for daily metric summaries (longer retention)
CREATE MATERIALIZED VIEW IF NOT EXISTS cirislens.metrics_daily
WITH (timescaledb.continuous) AS
SELECT
    agent_name,
    metric_name,
    time_bucket('1 day', timestamp) AS bucket,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    count(*) AS sample_count
FROM cirislens.agent_metrics
GROUP BY agent_name, metric_name, time_bucket('1 day', timestamp)
WITH NO DATA;

-- Refresh daily aggregates (keep 1 year of daily data)
SELECT add_continuous_aggregate_policy('cirislens.metrics_daily',
    start_offset => INTERVAL '365 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => true
);

-- Step 10: Verify setup
DO $$
DECLARE
    ht_count INTEGER;
    comp_count INTEGER;
    ret_count INTEGER;
BEGIN
    SELECT count(*) INTO ht_count FROM timescaledb_information.hypertables WHERE hypertable_schema = 'cirislens';
    SELECT count(*) INTO comp_count FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    SELECT count(*) INTO ret_count FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';

    RAISE NOTICE 'TimescaleDB Migration Complete:';
    RAISE NOTICE '  - Hypertables: %', ht_count;
    RAISE NOTICE '  - Compression policies: %', comp_count;
    RAISE NOTICE '  - Retention policies: %', ret_count;
END $$;
