-- Malformed Traces Audit Table
-- Security-focused logging for traces that fail schema validation
-- NEVER stores raw payload content - only metadata and hashes

CREATE TABLE IF NOT EXISTS cirislens.malformed_traces (
    -- Primary identification
    record_id UUID PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Claimed trace identification (may be spoofed)
    trace_id VARCHAR(256),
    source_ip VARCHAR(45),  -- IPv6 max length

    -- Schema validation results
    detected_event_types TEXT[],
    validation_errors TEXT[],
    validation_warnings TEXT[],

    -- Payload fingerprint (NEVER store actual content)
    payload_sha256 VARCHAR(64) NOT NULL,
    payload_size_bytes INTEGER,
    component_count INTEGER,

    -- Structural metadata (safe to store)
    has_signature BOOLEAN,
    signature_key_id VARCHAR(128),
    claimed_thought_id VARCHAR(256),
    claimed_task_id VARCHAR(256),

    -- Classification
    rejection_reason TEXT NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'error',

    -- Investigation tracking
    investigated BOOLEAN DEFAULT FALSE,
    investigation_notes TEXT,
    investigated_at TIMESTAMP WITH TIME ZONE,
    investigated_by VARCHAR(255)
);

-- Indexes for monitoring and investigation
CREATE INDEX IF NOT EXISTS idx_malformed_traces_timestamp
    ON cirislens.malformed_traces (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_malformed_traces_severity
    ON cirislens.malformed_traces (severity, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_malformed_traces_source_ip
    ON cirislens.malformed_traces (source_ip, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_malformed_traces_hash
    ON cirislens.malformed_traces (payload_sha256);

-- Composite index for attack pattern detection
CREATE INDEX IF NOT EXISTS idx_malformed_traces_attack_pattern
    ON cirislens.malformed_traces (source_ip, payload_sha256, timestamp DESC);

-- Comment
COMMENT ON TABLE cirislens.malformed_traces IS
    'Audit log for traces that fail schema validation. '
    'SECURITY: Only stores metadata and hashes, NEVER raw payload content. '
    'Used for attack detection, debugging, and forensic analysis.';

COMMENT ON COLUMN cirislens.malformed_traces.payload_sha256 IS
    'SHA-256 hash of the full payload. Allows correlation without storing content.';

COMMENT ON COLUMN cirislens.malformed_traces.severity IS
    'warning = validation warnings only, error = validation errors, critical = potential attack';

-- Retention policy: Keep 90 days for forensics
-- (Adjust based on compliance requirements)
-- SELECT add_retention_policy('cirislens.malformed_traces', INTERVAL '90 days');
