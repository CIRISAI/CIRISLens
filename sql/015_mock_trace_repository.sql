-- Mock Trace Repository for Development/Testing
-- Stores traces from mock LLMs separately from production corpus

-- Create mock traces table (same schema as covenant_traces)
CREATE TABLE IF NOT EXISTS cirislens.covenant_traces_mock (
    id BIGSERIAL,
    trace_id VARCHAR(128) PRIMARY KEY,

    -- Thought/Task identification
    thought_id VARCHAR(128),
    task_id VARCHAR(128),

    -- Agent identification (anonymized)
    agent_id_hash VARCHAR(64) NOT NULL,
    agent_name VARCHAR(255),

    -- Trace classification
    trace_type VARCHAR(50),
    cognitive_state VARCHAR(20),
    thought_type VARCHAR(50),
    thought_depth INTEGER,

    -- Timing from trace
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,

    -- The 6 trace components (stored as JSONB for flexibility)
    thought_start JSONB NOT NULL,
    snapshot_and_context JSONB NOT NULL,
    dma_results JSONB NOT NULL,
    aspdma_result JSONB NOT NULL,
    conscience_result JSONB NOT NULL,
    action_result JSONB NOT NULL,

    -- DMA scores denormalized
    csdma_plausibility_score NUMERIC(3,2),
    dsdma_domain_alignment NUMERIC(3,2),
    dsdma_domain VARCHAR(100),
    pdma_stakeholders TEXT,
    pdma_conflicts TEXT,

    -- Action selection rationale
    action_rationale TEXT,

    -- Conscience result denormalized
    conscience_passed BOOLEAN,
    action_was_overridden BOOLEAN,
    entropy_level NUMERIC(5,4),
    coherence_level NUMERIC(5,4),
    uncertainty_acknowledged BOOLEAN,
    reasoning_transparency NUMERIC(5,4),
    updated_status_detected BOOLEAN,
    thought_depth_triggered BOOLEAN,
    entropy_passed BOOLEAN,
    coherence_passed BOOLEAN,
    optimization_veto_passed BOOLEAN,
    epistemic_humility_passed BOOLEAN,

    -- Audit trail
    audit_entry_id UUID,
    audit_sequence_number BIGINT,
    audit_entry_hash VARCHAR(64),
    audit_signature TEXT,

    -- Action result denormalized
    selected_action VARCHAR(50),
    action_success BOOLEAN,
    processing_ms INTEGER,

    -- Resource usage
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_total INTEGER,
    cost_cents NUMERIC(10,4),
    carbon_grams NUMERIC(10,4),
    energy_mwh NUMERIC(10,6),
    llm_calls INTEGER,
    models_used TEXT[],

    -- IDMA fields
    idma_k_eff NUMERIC(5,2),
    idma_correlation_risk NUMERIC(3,2),
    idma_fragility_flag BOOLEAN,
    idma_phase VARCHAR(50),

    -- Signature fields
    signature TEXT,
    signature_key_id VARCHAR(128),
    signature_verified BOOLEAN,
    verification_error TEXT,

    -- PII scrubbing envelope
    original_content_hash VARCHAR(64),
    pii_scrubbed BOOLEAN DEFAULT FALSE,
    scrub_timestamp TIMESTAMP WITH TIME ZONE,
    scrub_signature TEXT,
    scrub_key_id VARCHAR(128),

    -- Consent tracking
    consent_timestamp TIMESTAMP WITH TIME ZONE,
    trace_level VARCHAR(20) DEFAULT 'generic',

    -- Mock-specific metadata
    mock_models TEXT[],              -- Which mock models were used
    mock_reason VARCHAR(255),        -- Why this was classified as mock

    -- Ingestion metadata
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    batch_id UUID
);

-- Index for querying mock traces
CREATE INDEX IF NOT EXISTS idx_mock_traces_timestamp ON cirislens.covenant_traces_mock (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mock_traces_agent ON cirislens.covenant_traces_mock (agent_name);
CREATE INDEX IF NOT EXISTS idx_mock_traces_models ON cirislens.covenant_traces_mock USING GIN (mock_models);

-- Comment
COMMENT ON TABLE cirislens.covenant_traces_mock IS 'Development/testing traces from mock LLMs - excluded from production scoring';
