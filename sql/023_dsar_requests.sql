-- DSAR (Data Subject Access Request) Tracking
-- Migration 023: Table for tracking trace deletion requests
-- Reference: GDPR Articles 17 (Right to Erasure) and 11(2) (Pseudonymized data)

-- ============================================================================
-- SECTION 1: DSAR Request Tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS cirislens.dsar_requests (
    id BIGSERIAL PRIMARY KEY,
    agent_id_hash VARCHAR(64) NOT NULL,          -- SHA-256 hash identifying the agent
    request_type VARCHAR(50) NOT NULL DEFAULT 'delete_all_traces',
    reason TEXT,
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE,

    -- Signature verification
    signature TEXT NOT NULL,                       -- Base64-encoded Ed25519 signature
    signature_key_id VARCHAR(64) NOT NULL,        -- Key ID used to sign the request
    signature_verified BOOLEAN DEFAULT FALSE,

    -- Processing results
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, processing, completed, failed
    traces_deleted INTEGER DEFAULT 0,
    error_message TEXT,

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_status CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    CONSTRAINT valid_request_type CHECK (request_type IN ('delete_all_traces'))
);

-- Index for looking up requests by agent
CREATE INDEX IF NOT EXISTS idx_dsar_requests_agent_id_hash
    ON cirislens.dsar_requests (agent_id_hash);

-- Index for processing queue
CREATE INDEX IF NOT EXISTS idx_dsar_requests_status
    ON cirislens.dsar_requests (status) WHERE status = 'pending';
