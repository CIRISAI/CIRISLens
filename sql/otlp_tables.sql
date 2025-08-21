-- OTLP Telemetry Storage Tables for CIRISLens
-- Stores metrics, traces, and logs from agent OTLP endpoints

-- Raw OTLP data storage (for replay/debugging)
CREATE TABLE IF NOT EXISTS otlp_telemetry (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metrics_data JSONB,
    traces_data JSONB,
    logs_data JSONB,
    INDEX idx_otlp_agent_time (agent_name, collected_at DESC)
);

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
CREATE INDEX IF NOT EXISTS idx_agent_metrics_time ON agent_metrics(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent ON agent_metrics(agent_name, metric_name, timestamp DESC);
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
    UNIQUE(trace_id, span_id),
    INDEX idx_traces_agent (agent_name, start_time DESC),
    INDEX idx_traces_operation (operation_name, start_time DESC),
    INDEX idx_traces_duration (duration_ms),
    INDEX idx_traces_attributes USING gin(attributes)
);

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
    component VARCHAR(255),
    INDEX idx_logs_agent_time (agent_name, timestamp DESC),
    INDEX idx_logs_severity (severity, timestamp DESC),
    INDEX idx_logs_trace (trace_id, span_id),
    INDEX idx_logs_attributes USING gin(attributes)
);

-- Collection errors tracking
CREATE TABLE IF NOT EXISTS collection_errors (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    error_message TEXT,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE,
    INDEX idx_collection_errors_agent (agent_name, occurred_at DESC)
);

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
    SELECT agent_name, COUNT(*) as error_count
    FROM collection_errors
    WHERE occurred_at > NOW() - INTERVAL '5 minutes'
      AND resolved_at IS NULL
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