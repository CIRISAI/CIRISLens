-- OTLP Telemetry Storage Tables for CIRISLens
-- Stores metrics, traces, and logs from agent OTLP endpoints

-- Raw OTLP data storage (for replay/debugging)
CREATE TABLE IF NOT EXISTS otlp_telemetry (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metrics_data JSONB,
    traces_data JSONB,
    logs_data JSONB
);

-- Create index for OTLP telemetry
CREATE INDEX IF NOT EXISTS idx_otlp_agent_time ON otlp_telemetry(agent_name, collected_at);

-- Time-series metrics storage
CREATE TABLE IF NOT EXISTS agent_metrics (
    agent_name VARCHAR(255) NOT NULL,
    metric_name VARCHAR(255) NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    labels JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (agent_name, metric_name, timestamp, labels)
);

-- Create hypertable for time-series data (if TimescaleDB is available)
-- SELECT create_hypertable('agent_metrics', 'timestamp', if_not_exists => TRUE);

-- Indexes for metric queries
CREATE INDEX IF NOT EXISTS idx_agent_metrics_time ON agent_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent ON agent_metrics(agent_name, metric_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_labels ON agent_metrics USING gin(labels);

-- Traces storage
CREATE TABLE IF NOT EXISTS agent_traces (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32) NOT NULL,
    parent_span_id VARCHAR(32),
    operation_name VARCHAR(500),
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (end_time - start_time)) * 1000
    ) STORED,
    attributes JSONB DEFAULT '{}'::jsonb,
    events JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(50),
    UNIQUE(trace_id, span_id)
);

-- Create indexes for traces
CREATE INDEX IF NOT EXISTS idx_traces_agent ON agent_traces(agent_name, start_time);
CREATE INDEX IF NOT EXISTS idx_traces_operation ON agent_traces(operation_name, start_time);
CREATE INDEX IF NOT EXISTS idx_traces_duration ON agent_traces(duration_ms);
CREATE INDEX IF NOT EXISTS idx_traces_attributes ON agent_traces USING gin(attributes);

-- Logs storage
CREATE TABLE IF NOT EXISTS agent_logs (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    severity VARCHAR(20) NOT NULL, -- DEBUG, INFO, WARNING, ERROR, CRITICAL
    message TEXT,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    attributes JSONB DEFAULT '{}'::jsonb,
    component VARCHAR(255)
);

-- Create indexes for logs
CREATE INDEX IF NOT EXISTS idx_logs_agent_time ON agent_logs(agent_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_severity ON agent_logs(severity, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_trace ON agent_logs(trace_id, span_id);
CREATE INDEX IF NOT EXISTS idx_logs_attributes ON agent_logs USING gin(attributes);

-- Collection errors tracking
CREATE TABLE IF NOT EXISTS collection_errors (
    id SERIAL PRIMARY KEY,
    source VARCHAR(255) NOT NULL,           -- e.g., 'manager_collector:primary', 'otlp_collector', 'agent:datum'
    error_type VARCHAR(100) NOT NULL,       -- e.g., 'DISCOVERY_FAILURE', 'NETWORK_ERROR', 'SSL_ERROR'
    error_message TEXT NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE
);

-- Create indexes for collection errors
CREATE INDEX IF NOT EXISTS idx_collection_errors_source_time ON collection_errors(source, occurred_at);
CREATE INDEX IF NOT EXISTS idx_collection_errors_type ON collection_errors(error_type);
CREATE INDEX IF NOT EXISTS idx_collection_errors_unresolved ON collection_errors(occurred_at) WHERE resolved_at IS NULL;

-- Agent configuration (for discovered OTLP endpoints)
CREATE TABLE IF NOT EXISTS agent_otlp_configs (
    agent_name VARCHAR(255) PRIMARY KEY,
    base_url VARCHAR(512) NOT NULL,
    auth_token TEXT,
    enabled BOOLEAN DEFAULT true,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_successful_collection TIMESTAMP WITH TIME ZONE,
    collection_interval_seconds INTEGER DEFAULT 30,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Insert Datum configuration if not exists
INSERT INTO agent_otlp_configs (agent_name, base_url, auth_token, enabled)
VALUES (
    'datum',
    'https://agents.ciris.ai/api/datum',
    'service:YOUR_TOKEN_HERE',
    true
) ON CONFLICT (agent_name) DO UPDATE
SET auth_token = EXCLUDED.auth_token,
    base_url = EXCLUDED.base_url;

-- Views for easier querying

-- Current agent health view
CREATE OR REPLACE VIEW agent_health_status AS
SELECT 
    a.agent_name,
    a.enabled,
    a.last_successful_collection,
    COALESCE(m.metric_count, 0) as recent_metric_count,
    COALESCE(t.trace_count, 0) as recent_trace_count,
    COALESCE(l.log_count, 0) as recent_log_count,
    COALESCE(e.error_count, 0) as recent_error_count,
    CASE 
        WHEN a.last_successful_collection > NOW() - INTERVAL '5 minutes' 
             AND COALESCE(e.error_count, 0) = 0 
        THEN 'healthy'
        WHEN a.last_successful_collection > NOW() - INTERVAL '15 minutes'
        THEN 'degraded'
        ELSE 'unhealthy'
    END as health_status
FROM agent_otlp_configs a
LEFT JOIN (
    SELECT agent_name, COUNT(*) as metric_count
    FROM agent_metrics
    WHERE timestamp > NOW() - INTERVAL '5 minutes'
    GROUP BY agent_name
) m ON a.agent_name = m.agent_name
LEFT JOIN (
    SELECT agent_name, COUNT(*) as trace_count
    FROM agent_traces
    WHERE start_time > NOW() - INTERVAL '5 minutes'
    GROUP BY agent_name
) t ON a.agent_name = t.agent_name
LEFT JOIN (
    SELECT agent_name, COUNT(*) as log_count
    FROM agent_logs
    WHERE timestamp > NOW() - INTERVAL '5 minutes'
    GROUP BY agent_name
) l ON a.agent_name = l.agent_name
LEFT JOIN (
    SELECT
        CASE
            WHEN source LIKE 'agent:%' THEN SUBSTRING(source FROM 7)  -- Extract agent name from 'agent:name'
            ELSE NULL
        END as agent_name,
        COUNT(*) as error_count
    FROM collection_errors
    WHERE occurred_at > NOW() - INTERVAL '5 minutes'
      AND resolved_at IS NULL
      AND source LIKE 'agent:%'
    GROUP BY agent_name
) e ON a.agent_name = e.agent_name;

-- Metric aggregations view
CREATE OR REPLACE VIEW metric_aggregations AS
SELECT 
    agent_name,
    metric_name,
    DATE_TRUNC('minute', timestamp) as minute,
    AVG(value) as avg_value,
    MIN(value) as min_value,
    MAX(value) as max_value,
    COUNT(*) as sample_count
FROM agent_metrics
WHERE timestamp > NOW() - INTERVAL '1 hour'
GROUP BY agent_name, metric_name, DATE_TRUNC('minute', timestamp)
ORDER BY minute DESC;