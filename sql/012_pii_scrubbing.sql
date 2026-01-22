-- PII Scrubbing Envelope for Covenant Traces
-- Migration 012: Add cryptographic envelope fields for PII-scrubbed full_traces
-- Reference: https://ciris.ai/privacy, https://ciris.ai/coherence-ratchet

-- ============================================================================
-- SECTION 1: Add PII Scrubbing Envelope Columns
-- ============================================================================

-- Add columns for the cryptographic envelope that proves provenance
-- while allowing us to delete original PII-containing data

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS original_content_hash VARCHAR(64),
ADD COLUMN IF NOT EXISTS pii_scrubbed BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS scrub_timestamp TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS scrub_signature TEXT,
ADD COLUMN IF NOT EXISTS scrub_key_id VARCHAR(64);

-- Add comment explaining the fields
COMMENT ON COLUMN cirislens.covenant_traces.original_content_hash IS 'SHA-256 hash of original trace content before PII scrubbing - proves we had the original';
COMMENT ON COLUMN cirislens.covenant_traces.pii_scrubbed IS 'Whether PII was scrubbed from this trace (only for full_traces level)';
COMMENT ON COLUMN cirislens.covenant_traces.scrub_timestamp IS 'When PII scrubbing was performed';
COMMENT ON COLUMN cirislens.covenant_traces.scrub_signature IS 'CIRISLens Ed25519 signature of scrubbed content';
COMMENT ON COLUMN cirislens.covenant_traces.scrub_key_id IS 'ID of CIRISLens key used to sign scrubbed content';

-- ============================================================================
-- SECTION 2: Index for Scrubbed Traces
-- ============================================================================

-- Index for finding scrubbed full_traces (case law candidates)
CREATE INDEX IF NOT EXISTS idx_traces_scrubbed
ON cirislens.covenant_traces(pii_scrubbed, trace_level, timestamp DESC)
WHERE pii_scrubbed = TRUE;

-- ============================================================================
-- SECTION 3: CIRISLens Scrub Signing Keys
-- ============================================================================

-- Store CIRISLens signing keys (separate from agent keys)
CREATE TABLE IF NOT EXISTS cirislens.lens_signing_keys (
    key_id VARCHAR(64) PRIMARY KEY,           -- e.g., "lens-scrub-v1"
    public_key_base64 VARCHAR(128) NOT NULL,  -- Base64-encoded Ed25519 public key
    key_type VARCHAR(50) NOT NULL,            -- scrub, audit, etc.
    description TEXT,

    -- Key lifecycle
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_reason TEXT,

    CONSTRAINT valid_key_type CHECK (key_type IN ('scrub', 'audit', 'api'))
);

COMMENT ON TABLE cirislens.lens_signing_keys IS 'CIRISLens signing keys for PII scrubbing and other operations';

-- Grant permissions
GRANT ALL PRIVILEGES ON cirislens.lens_signing_keys TO cirislens;

-- ============================================================================
-- SECTION 4: Case Law Compendium Staging
-- ============================================================================

-- Staging table for traces being evaluated for case law inclusion
CREATE TABLE IF NOT EXISTS cirislens.case_law_candidates (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(128) NOT NULL,
    trace_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Evaluation criteria
    pattern_type VARCHAR(100),                -- e.g., "conscience_override", "fragility_detection", "wbd_deferral"
    pattern_description TEXT,

    -- Evaluation status
    status VARCHAR(20) DEFAULT 'pending',     -- pending, approved, rejected
    evaluated_at TIMESTAMP WITH TIME ZONE,
    evaluated_by VARCHAR(255),
    evaluation_notes TEXT,

    -- Publication
    published BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMP WITH TIME ZONE,
    compendium_id VARCHAR(100),               -- ID in published compendium

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_status CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT fk_trace FOREIGN KEY (trace_id, trace_timestamp)
        REFERENCES cirislens.covenant_traces(trace_id, timestamp) ON DELETE CASCADE
);

COMMENT ON TABLE cirislens.case_law_candidates IS 'Staging table for traces being evaluated for inclusion in the Coherence Ratchet case law compendium';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_case_law_status ON cirislens.case_law_candidates(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_case_law_pattern ON cirislens.case_law_candidates(pattern_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_case_law_published ON cirislens.case_law_candidates(published, published_at DESC)
    WHERE published = TRUE;

-- Grant permissions
GRANT ALL PRIVILEGES ON cirislens.case_law_candidates TO cirislens;
GRANT USAGE, SELECT ON SEQUENCE cirislens.case_law_candidates_id_seq TO cirislens;

-- ============================================================================
-- SECTION 5: Verification
-- ============================================================================

DO $$
DECLARE
    col_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_schema = 'cirislens'
    AND table_name = 'covenant_traces'
    AND column_name IN ('original_content_hash', 'pii_scrubbed', 'scrub_timestamp', 'scrub_signature', 'scrub_key_id');

    RAISE NOTICE 'PII Scrubbing Migration Complete:';
    RAISE NOTICE '  - New columns added to covenant_traces: %', col_count;
    RAISE NOTICE '  - New tables: lens_signing_keys, case_law_candidates';
    RAISE NOTICE '  - Cryptographic envelope preserves provenance while deleting PII';
END $$;
