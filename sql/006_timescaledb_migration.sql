-- TimescaleDB Migration for CIRISLens
-- Converts existing tables to hypertables with automatic compression and retention
-- Fully idempotent - safe to run multiple times

-- Step 1: Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Step 2: Convert agent_metrics to hypertable (if table exists and not already hypertable)
DO $$
BEGIN
    -- Only proceed if table exists and is not already a hypertable
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'cirislens' AND table_name = 'agent_metrics')
       AND NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_metrics') THEN

        -- Drop the existing primary key (hypertables need time column in PK)
        ALTER TABLE cirislens.agent_metrics DROP CONSTRAINT IF EXISTS agent_metrics_pkey;

        -- Create hypertable with automatic chunking (7 day intervals)
        PERFORM create_hypertable('cirislens.agent_metrics', 'timestamp',
            chunk_time_interval => INTERVAL '7 days',
            migrate_data => true,
            if_not_exists => true
        );

        RAISE NOTICE 'Converted agent_metrics to hypertable';
    ELSE
        RAISE NOTICE 'agent_metrics: skipped (not exists or already hypertable)';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_metrics hypertable: % (continuing)', SQLERRM;
END $$;

-- Step 3: Convert agent_logs to hypertable
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'cirislens' AND table_name = 'agent_logs')
       AND NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_logs') THEN
        PERFORM create_hypertable('cirislens.agent_logs', 'timestamp',
            chunk_time_interval => INTERVAL '1 day',
            migrate_data => true,
            if_not_exists => true
        );
        RAISE NOTICE 'Converted agent_logs to hypertable';
    ELSE
        RAISE NOTICE 'agent_logs: skipped (not exists or already hypertable)';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_logs hypertable: % (continuing)', SQLERRM;
END $$;

-- Step 4: Convert agent_traces to hypertable
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'cirislens' AND table_name = 'agent_traces')
       AND NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_traces') THEN
        PERFORM create_hypertable('cirislens.agent_traces', 'start_time',
            chunk_time_interval => INTERVAL '1 day',
            migrate_data => true,
            if_not_exists => true
        );
        RAISE NOTICE 'Converted agent_traces to hypertable';
    ELSE
        RAISE NOTICE 'agent_traces: skipped (not exists or already hypertable)';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_traces hypertable: % (continuing)', SQLERRM;
END $$;

-- Step 5: Enable compression on hypertables (idempotent - ignores if already set)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_metrics') THEN
        ALTER TABLE cirislens.agent_metrics SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'agent_name,metric_name',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_metrics compression: % (continuing)', SQLERRM;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_logs') THEN
        ALTER TABLE cirislens.agent_logs SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'agent_name,severity',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_logs compression: % (continuing)', SQLERRM;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_traces') THEN
        ALTER TABLE cirislens.agent_traces SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'agent_name',
            timescaledb.compress_orderby = 'start_time DESC'
        );
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'agent_traces compression: % (continuing)', SQLERRM;
END $$;

-- Step 6: Add compression policies (if_not_exists handles idempotency)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_metrics') THEN
        PERFORM add_compression_policy('cirislens.agent_metrics', INTERVAL '7 days', if_not_exists => true);
    END IF;
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_logs') THEN
        PERFORM add_compression_policy('cirislens.agent_logs', INTERVAL '7 days', if_not_exists => true);
    END IF;
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_traces') THEN
        PERFORM add_compression_policy('cirislens.agent_traces', INTERVAL '7 days', if_not_exists => true);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Compression policies: % (continuing)', SQLERRM;
END $$;

-- Step 7: Add retention policies
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_metrics') THEN
        PERFORM add_retention_policy('cirislens.agent_metrics', INTERVAL '30 days', if_not_exists => true);
    END IF;
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_logs') THEN
        PERFORM add_retention_policy('cirislens.agent_logs', INTERVAL '14 days', if_not_exists => true);
    END IF;
    IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_traces') THEN
        PERFORM add_retention_policy('cirislens.agent_traces', INTERVAL '14 days', if_not_exists => true);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Retention policies: % (continuing)', SQLERRM;
END $$;

-- Step 8: Create continuous aggregate for hourly metric summaries (skip if exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.continuous_aggregates WHERE view_name = 'metrics_hourly') THEN
        CREATE MATERIALIZED VIEW cirislens.metrics_hourly
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

        PERFORM add_continuous_aggregate_policy('cirislens.metrics_hourly',
            start_offset => INTERVAL '90 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour',
            if_not_exists => true
        );
        RAISE NOTICE 'Created metrics_hourly continuous aggregate';
    ELSE
        RAISE NOTICE 'metrics_hourly: already exists';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'metrics_hourly: % (continuing)', SQLERRM;
END $$;

-- Step 9: Create continuous aggregate for daily metric summaries (skip if exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.continuous_aggregates WHERE view_name = 'metrics_daily') THEN
        CREATE MATERIALIZED VIEW cirislens.metrics_daily
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

        PERFORM add_continuous_aggregate_policy('cirislens.metrics_daily',
            start_offset => INTERVAL '365 days',
            end_offset => INTERVAL '1 day',
            schedule_interval => INTERVAL '1 day',
            if_not_exists => true
        );
        RAISE NOTICE 'Created metrics_daily continuous aggregate';
    ELSE
        RAISE NOTICE 'metrics_daily: already exists';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'metrics_daily: % (continuing)', SQLERRM;
END $$;

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
