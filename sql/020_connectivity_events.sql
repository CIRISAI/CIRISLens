-- Migration 020: Add connectivity_events table for agent startup/shutdown tracking

CREATE TABLE IF NOT EXISTS cirislens.connectivity_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trace_id VARCHAR(256),
    event_type VARCHAR(20) NOT NULL,  -- 'startup' or 'shutdown'
    agent_id VARCHAR(256),
    agent_name VARCHAR(256),
    agent_id_hash VARCHAR(64),

    -- Event data
    event_data JSONB,

    -- Signature verification
    signature TEXT,
    signature_key_id VARCHAR(128),
    signature_verified BOOLEAN DEFAULT FALSE,

    -- Metadata
    consent_timestamp TIMESTAMPTZ,
    trace_level VARCHAR(20)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_connectivity_timestamp
ON cirislens.connectivity_events (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_connectivity_agent
ON cirislens.connectivity_events (agent_name, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_connectivity_type
ON cirislens.connectivity_events (event_type, timestamp DESC);

-- Comments
COMMENT ON TABLE cirislens.connectivity_events IS 'Agent startup/shutdown connectivity events';
COMMENT ON COLUMN cirislens.connectivity_events.event_type IS 'Event type: startup or shutdown';
COMMENT ON COLUMN cirislens.connectivity_events.event_data IS 'Full event data JSON from the connectivity event';
