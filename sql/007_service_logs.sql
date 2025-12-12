-- Service Logs Migration for CIRISLens
-- Adds service_logs table for centralized log aggregation from Billing, Proxy, Manager
-- And service_tokens table for authentication

-- Service tokens for log ingestion authentication
CREATE TABLE IF NOT EXISTS cirislens.service_tokens (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL UNIQUE,
    token_hash VARCHAR(64) NOT NULL,  -- SHA-256 hash of token
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by VARCHAR(255),
    last_used_at TIMESTAMPTZ,
    enabled BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_service_tokens_name ON cirislens.service_tokens(service_name);
CREATE INDEX IF NOT EXISTS idx_service_tokens_enabled ON cirislens.service_tokens(enabled) WHERE enabled = TRUE;

-- Service logs table
CREATE TABLE IF NOT EXISTS cirislens.service_logs (
    id BIGSERIAL,
    service_name VARCHAR(100) NOT NULL,
    server_id VARCHAR(50),
    timestamp TIMESTAMPTZ NOT NULL,
    level VARCHAR(20) NOT NULL,
    event VARCHAR(255),
    logger VARCHAR(255),
    message TEXT,
    request_id VARCHAR(64),
    trace_id VARCHAR(64),
    user_hash VARCHAR(16),
    attributes JSONB DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (timestamp, id)
);

-- Convert to hypertable (TimescaleDB)
SELECT create_hypertable('cirislens.service_logs', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_service_logs_service
    ON cirislens.service_logs(service_name, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_service_logs_level
    ON cirislens.service_logs(level, timestamp DESC)
    WHERE level IN ('ERROR', 'CRITICAL', 'WARNING');

CREATE INDEX IF NOT EXISTS idx_service_logs_event
    ON cirislens.service_logs(event, timestamp DESC)
    WHERE event IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_service_logs_request
    ON cirislens.service_logs(request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_service_logs_trace
    ON cirislens.service_logs(trace_id)
    WHERE trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_service_logs_attrs
    ON cirislens.service_logs USING gin(attributes);

-- Compression policy (after 7 days)
SELECT add_compression_policy('cirislens.service_logs', INTERVAL '7 days', if_not_exists => TRUE);

-- Retention policy (90 days for service logs - longer than agent logs due to compliance)
SELECT add_retention_policy('cirislens.service_logs', INTERVAL '90 days', if_not_exists => TRUE);

-- Enable compression settings
ALTER TABLE cirislens.service_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'service_name,level',
    timescaledb.compress_orderby = 'timestamp DESC'
);

-- Grant permissions
GRANT ALL PRIVILEGES ON cirislens.service_logs TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.service_tokens TO cirislens;
GRANT USAGE, SELECT ON SEQUENCE cirislens.service_logs_id_seq TO cirislens;
GRANT USAGE, SELECT ON SEQUENCE cirislens.service_tokens_id_seq TO cirislens;

-- Verify setup
DO $$
DECLARE
    ht_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'service_logs'
    ) INTO ht_exists;

    IF ht_exists THEN
        RAISE NOTICE 'service_logs hypertable created successfully';
    ELSE
        RAISE WARNING 'service_logs hypertable creation may have failed';
    END IF;
END $$;
