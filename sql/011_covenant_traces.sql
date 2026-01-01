-- CIRIS Covenant Trace Storage
-- Migration 011: Tables for Ed25519-signed reasoning traces
-- Reference: FSD/covenant_events_receiver.md

-- ============================================================================
-- SECTION 1: Root Public Keys for Signature Verification
-- Reference: Covenant Section I - Chain of Trust
-- ============================================================================

-- Public keys for Ed25519 signature verification
CREATE TABLE IF NOT EXISTS cirislens.covenant_public_keys (
    key_id VARCHAR(64) PRIMARY KEY,           -- e.g., "wa-2025-06-14-ROOT00"
    public_key_base64 VARCHAR(128) NOT NULL,  -- Base64-encoded Ed25519 public key
    algorithm VARCHAR(20) DEFAULT 'Ed25519',
    description TEXT,

    -- Key lifecycle
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_reason TEXT,

    -- Audit
    added_by VARCHAR(255),

    CONSTRAINT valid_algorithm CHECK (algorithm IN ('Ed25519'))
);

-- ============================================================================
-- SECTION 2: Covenant Traces Storage
-- Reference: FSD Section 3 - Trace Structure (6 components)
-- ============================================================================

-- Main trace storage - immutable record of agent reasoning
CREATE TABLE IF NOT EXISTS cirislens.covenant_traces (
    id BIGSERIAL,                             -- Internal ID for partitioning
    trace_id VARCHAR(128) PRIMARY KEY,        -- e.g., "trace-th_std_xxxxx-20251231180909"

    -- Thought/Task identification
    thought_id VARCHAR(128),                  -- e.g., "th_followup_th_std_2_b44ae980-ab8"
    task_id VARCHAR(128),                     -- e.g., "VERIFY_IDENTITY_2e1cbbd8-..."

    -- Agent identification (anonymized)
    agent_id_hash VARCHAR(64) NOT NULL,       -- From trace, pre-hashed by agent
    agent_name VARCHAR(255),                  -- Agent identity name (e.g., "Datum")

    -- Trace classification
    trace_type VARCHAR(50),                   -- VERIFY_IDENTITY, VALIDATE_INTEGRITY, etc.
    cognitive_state VARCHAR(20),              -- WORK, DREAM, PLAY, SOLITUDE, WAKEUP
    thought_type VARCHAR(50),                 -- standard, follow_up, etc.
    thought_depth INTEGER,                    -- 0=initial, higher=follow-up

    -- Timing from trace
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,

    -- The 6 trace components (stored as JSONB for flexibility)
    thought_start JSONB NOT NULL,             -- component_type: observation
    snapshot_and_context JSONB NOT NULL,      -- component_type: context
    dma_results JSONB NOT NULL,               -- component_type: rationale (3 DMAs)
    aspdma_result JSONB NOT NULL,             -- component_type: rationale (action selection)
    conscience_result JSONB NOT NULL,         -- component_type: conscience
    action_result JSONB NOT NULL,             -- component_type: action

    -- DMA scores denormalized for Coherence Ratchet analysis
    csdma_plausibility_score NUMERIC(3,2),    -- 0.00-1.00 Common Sense DMA
    dsdma_domain_alignment NUMERIC(3,2),      -- 0.00-1.00 Domain-Specific DMA
    dsdma_domain VARCHAR(100),                -- Domain name (e.g., "Datum")
    pdma_stakeholders TEXT,                   -- Stakeholders identified
    pdma_conflicts TEXT,                      -- Conflicts identified

    -- Action selection rationale (for semantic analysis)
    action_rationale TEXT,                    -- Natural language justification
    action_rationale_embedding VECTOR(1536),  -- Optional: OpenAI ada-002 embedding

    -- Conscience result denormalized for fast queries
    conscience_passed BOOLEAN,                -- Overall conscience pass/fail
    action_was_overridden BOOLEAN,            -- Was action changed by conscience?
    -- Epistemic data
    entropy_level NUMERIC(5,4),               -- 0.0000-1.0000
    coherence_level NUMERIC(5,4),             -- 0.0000-1.0000
    uncertainty_acknowledged BOOLEAN,
    reasoning_transparency NUMERIC(5,4),
    -- Bypass guardrails
    updated_status_detected BOOLEAN,
    thought_depth_triggered BOOLEAN,
    -- Ethical faculties (null if skipped)
    entropy_passed BOOLEAN,
    coherence_passed BOOLEAN,
    optimization_veto_passed BOOLEAN,
    epistemic_humility_passed BOOLEAN,

    -- Action metadata denormalized
    selected_action VARCHAR(50),              -- SPEAK, TOOL, OBSERVE, DEFER, etc.
    action_success BOOLEAN,

    -- Audit trail from ACTION_RESULT
    audit_entry_id UUID,
    audit_sequence_number BIGINT,             -- For hash chain verification
    audit_entry_hash VARCHAR(64),             -- SHA-256 of audit entry
    audit_signature TEXT,                     -- RSA signature of audit entry

    -- Resource usage
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_total INTEGER,
    cost_cents NUMERIC(10,5),
    carbon_grams NUMERIC(10,5),
    energy_mwh NUMERIC(15,5),
    llm_calls INTEGER,
    models_used TEXT[],                       -- Array of model names

    -- Trace-level cryptographic verification
    signature TEXT NOT NULL,                  -- Base64 Ed25519 signature of trace
    signer_key_id VARCHAR(64) NOT NULL,       -- References covenant_public_keys
    signature_verified BOOLEAN DEFAULT FALSE,
    verification_error TEXT,

    -- Consent tracking
    consent_timestamp TIMESTAMP WITH TIME ZONE,

    -- Processing timing
    processing_ms INTEGER,                    -- From action_result.execution_time_ms
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,  -- When trace was created
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('cirislens.covenant_traces', 'timestamp',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================================================
-- SECTION 3: Trace Batches (for tracking ingestion)
-- ============================================================================

CREATE TABLE IF NOT EXISTS cirislens.covenant_trace_batches (
    batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Batch metadata
    batch_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    consent_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Results
    traces_received INTEGER NOT NULL,
    traces_accepted INTEGER NOT NULL,
    traces_rejected INTEGER NOT NULL,
    rejection_reasons JSONB,

    -- Source
    source_ip INET,

    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- SECTION 4: Indexes for Common Queries
-- ============================================================================

-- Time-based queries (primary access pattern)
CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON cirislens.covenant_traces(timestamp DESC);

-- Agent queries
CREATE INDEX IF NOT EXISTS idx_traces_agent ON cirislens.covenant_traces(agent_id_hash, timestamp DESC);

-- Task/Thought queries
CREATE INDEX IF NOT EXISTS idx_traces_task ON cirislens.covenant_traces(task_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_traces_thought ON cirislens.covenant_traces(thought_id);

-- Trace type queries (for wakeup trace analysis)
CREATE INDEX IF NOT EXISTS idx_traces_type ON cirislens.covenant_traces(trace_type, timestamp DESC);

-- DMA score queries (for Coherence Ratchet anomaly detection)
CREATE INDEX IF NOT EXISTS idx_traces_csdma ON cirislens.covenant_traces(csdma_plausibility_score, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_traces_dsdma ON cirislens.covenant_traces(dsdma_domain_alignment, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_traces_domain ON cirislens.covenant_traces(dsdma_domain, timestamp DESC);

-- Conscience result queries (for coherence ratchet analysis)
CREATE INDEX IF NOT EXISTS idx_traces_conscience ON cirislens.covenant_traces(
    conscience_passed, entropy_passed, coherence_passed, optimization_veto_passed, epistemic_humility_passed
);

-- Epistemic data queries (for coherence level analysis)
CREATE INDEX IF NOT EXISTS idx_traces_epistemic ON cirislens.covenant_traces(
    entropy_level, coherence_level
) WHERE entropy_level IS NOT NULL;

-- Action queries
CREATE INDEX IF NOT EXISTS idx_traces_action ON cirislens.covenant_traces(selected_action, timestamp DESC);

-- Signature verification status
CREATE INDEX IF NOT EXISTS idx_traces_unverified ON cirislens.covenant_traces(signature_verified)
    WHERE signature_verified = FALSE;

-- Audit sequence for hash chain verification
CREATE INDEX IF NOT EXISTS idx_traces_audit_seq ON cirislens.covenant_traces(agent_id_hash, audit_sequence_number);

-- Full-text search on action rationale (for semantic pattern matching)
CREATE INDEX IF NOT EXISTS idx_traces_rationale_gin ON cirislens.covenant_traces
    USING gin(to_tsvector('english', action_rationale));

-- Vector similarity search (requires pgvector extension)
-- CREATE INDEX IF NOT EXISTS idx_traces_embedding ON cirislens.covenant_traces
--     USING ivfflat (action_rationale_embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================================
-- SECTION 5: Metrics Tracking
-- ============================================================================

-- Aggregated metrics for dashboard
CREATE TABLE IF NOT EXISTS cirislens.covenant_trace_metrics (
    time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Counters
    traces_received_total BIGINT DEFAULT 0,
    traces_accepted_total BIGINT DEFAULT 0,
    traces_rejected_invalid_sig BIGINT DEFAULT 0,
    traces_rejected_unknown_key BIGINT DEFAULT 0,
    traces_rejected_invalid_json BIGINT DEFAULT 0,

    -- By trace type
    traces_verify_identity BIGINT DEFAULT 0,
    traces_validate_integrity BIGINT DEFAULT 0,
    traces_evaluate_resilience BIGINT DEFAULT 0,
    traces_accept_incompleteness BIGINT DEFAULT 0,
    traces_express_gratitude BIGINT DEFAULT 0,
    traces_other BIGINT DEFAULT 0,

    -- Processing
    avg_processing_ms NUMERIC,

    PRIMARY KEY (time)
);

SELECT create_hypertable('cirislens.covenant_trace_metrics', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ============================================================================
-- SECTION 6: Continuous Aggregates for Dashboard
-- ============================================================================

-- Hourly trace summary
CREATE MATERIALIZED VIEW IF NOT EXISTS cirislens.covenant_traces_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp) AS hour,
    COUNT(*) as trace_count,
    COUNT(DISTINCT agent_id_hash) as unique_agents,

    -- Trace types
    COUNT(*) FILTER (WHERE trace_type = 'VERIFY_IDENTITY') as verify_identity_count,
    COUNT(*) FILTER (WHERE trace_type = 'VALIDATE_INTEGRITY') as validate_integrity_count,
    COUNT(*) FILTER (WHERE trace_type = 'EVALUATE_RESILIENCE') as evaluate_resilience_count,
    COUNT(*) FILTER (WHERE trace_type = 'ACCEPT_INCOMPLETENESS') as accept_incompleteness_count,
    COUNT(*) FILTER (WHERE trace_type = 'EXPRESS_GRATITUDE') as express_gratitude_count,

    -- DMA scores (for Coherence Ratchet trend analysis)
    AVG(csdma_plausibility_score) as avg_csdma_plausibility,
    MIN(csdma_plausibility_score) as min_csdma_plausibility,
    AVG(dsdma_domain_alignment) as avg_dsdma_alignment,
    MIN(dsdma_domain_alignment) as min_dsdma_alignment,

    -- Epistemic metrics
    AVG(entropy_level) as avg_entropy_level,
    AVG(coherence_level) as avg_coherence_level,

    -- Conscience pass rates
    AVG(CASE WHEN conscience_passed THEN 1.0 ELSE 0.0 END) as conscience_pass_rate,
    AVG(CASE WHEN entropy_passed THEN 1.0 ELSE 0.0 END) as entropy_pass_rate,
    AVG(CASE WHEN coherence_passed THEN 1.0 ELSE 0.0 END) as coherence_pass_rate,
    AVG(CASE WHEN optimization_veto_passed THEN 1.0 ELSE 0.0 END) as optimization_veto_pass_rate,
    AVG(CASE WHEN epistemic_humility_passed THEN 1.0 ELSE 0.0 END) as epistemic_humility_pass_rate,

    -- Override tracking (conscience changed the action)
    COUNT(*) FILTER (WHERE action_was_overridden = TRUE) as overrides_count,

    -- Actions
    COUNT(*) FILTER (WHERE selected_action = 'SPEAK') as speak_count,
    COUNT(*) FILTER (WHERE selected_action = 'DEFER') as defer_count,
    COUNT(*) FILTER (WHERE selected_action = 'PONDER') as ponder_count,
    COUNT(*) FILTER (WHERE selected_action = 'TASK_COMPLETE') as task_complete_count,

    -- Resource usage
    SUM(tokens_total) as total_tokens,
    SUM(cost_cents) as total_cost_cents,
    SUM(llm_calls) as total_llm_calls,

    -- Performance
    AVG(processing_ms) as avg_processing_ms,
    MAX(processing_ms) as max_processing_ms

FROM cirislens.covenant_traces
WHERE signature_verified = TRUE
GROUP BY hour
WITH NO DATA;

-- Refresh policy: update hourly aggregates every 5 minutes
SELECT add_continuous_aggregate_policy('cirislens.covenant_traces_hourly',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- ============================================================================
-- SECTION 7: Retention Policy
-- ============================================================================

-- Keep detailed traces for 90 days (adjustable)
SELECT add_retention_policy('cirislens.covenant_traces',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- Compress after 7 days
SELECT add_compression_policy('cirislens.covenant_traces',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Keep hourly aggregates for 1 year
SELECT add_retention_policy('cirislens.covenant_traces_hourly',
    drop_after => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- ============================================================================
-- SECTION 8: Permissions
-- ============================================================================

GRANT ALL PRIVILEGES ON cirislens.covenant_public_keys TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.covenant_traces TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.covenant_trace_batches TO cirislens;
GRANT ALL PRIVILEGES ON cirislens.covenant_trace_metrics TO cirislens;
GRANT SELECT ON cirislens.covenant_traces_hourly TO cirislens;

-- ============================================================================
-- SECTION 9: Verification
-- ============================================================================

DO $$
DECLARE
    table_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables
    WHERE table_schema = 'cirislens'
    AND table_name IN ('covenant_public_keys', 'covenant_traces', 'covenant_trace_batches', 'covenant_trace_metrics');

    RAISE NOTICE 'Covenant Traces Migration Complete:';
    RAISE NOTICE '  - New tables created: %', table_count;
    RAISE NOTICE '  - Tables: covenant_public_keys, covenant_traces, covenant_trace_batches, covenant_trace_metrics';
    RAISE NOTICE '  - Hypertables enabled with 7-day chunks';
    RAISE NOTICE '  - Retention: 90 days detail, 1 year aggregates';
    RAISE NOTICE '  - Compression: after 7 days';
END $$;
