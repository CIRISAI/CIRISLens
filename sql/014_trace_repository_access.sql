-- Trace Repository Access Control
-- Migration 014: Add public_sample and partner_access columns for RBAC
-- Reference: FSD/trace_repository_api.md

-- ============================================================================
-- SECTION 1: Access Control Columns
-- ============================================================================

-- Public sample flag for ciris.ai/explore-a-trace
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS public_sample BOOLEAN DEFAULT FALSE;

-- Partner access array for sharing traces with specific partners
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS partner_access TEXT[] DEFAULT '{}';

-- Metadata for access control changes
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS access_updated_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS access_updated_by VARCHAR(100);

COMMENT ON COLUMN cirislens.covenant_traces.public_sample IS 'If true, trace is visible to public access level (ciris.ai/explore-a-trace)';
COMMENT ON COLUMN cirislens.covenant_traces.partner_access IS 'Array of partner IDs that have access to this trace';
COMMENT ON COLUMN cirislens.covenant_traces.access_updated_at IS 'When access controls were last modified';
COMMENT ON COLUMN cirislens.covenant_traces.access_updated_by IS 'User who last modified access controls';

-- ============================================================================
-- SECTION 2: Indexes for Access Control Queries
-- ============================================================================

-- Index for public sample queries (powers ciris.ai/explore-a-trace)
CREATE INDEX IF NOT EXISTS idx_traces_public_sample
ON cirislens.covenant_traces(timestamp DESC)
WHERE public_sample = TRUE;

-- GIN index for partner access array queries
CREATE INDEX IF NOT EXISTS idx_traces_partner_access
ON cirislens.covenant_traces USING GIN(partner_access)
WHERE partner_access != '{}';

-- Composite index for partner queries (own agents + samples + partner-tagged)
CREATE INDEX IF NOT EXISTS idx_traces_agent_id_hash
ON cirislens.covenant_traces(agent_id_hash, timestamp DESC)
WHERE agent_id_hash IS NOT NULL;

-- ============================================================================
-- SECTION 3: Access Control Audit Log
-- ============================================================================

CREATE TABLE IF NOT EXISTS cirislens.trace_access_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Who accessed
    user_id VARCHAR(100) NOT NULL,
    access_level VARCHAR(20) NOT NULL,  -- full, partner, public
    partner_id VARCHAR(100),            -- for partner access

    -- What was accessed
    endpoint VARCHAR(100) NOT NULL,
    query_params JSONB,
    trace_ids_returned TEXT[],
    traces_count INTEGER,

    -- Request metadata
    ip_address INET,
    user_agent TEXT,

    CONSTRAINT valid_access_level CHECK (access_level IN ('full', 'partner', 'public'))
);

COMMENT ON TABLE cirislens.trace_access_log IS 'Audit log for trace repository access';

-- Index for audit queries
CREATE INDEX IF NOT EXISTS idx_trace_access_log_timestamp
ON cirislens.trace_access_log(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_trace_access_log_user
ON cirislens.trace_access_log(user_id, timestamp DESC);

-- Grant permissions
GRANT ALL PRIVILEGES ON cirislens.trace_access_log TO cirislens;
GRANT USAGE, SELECT ON SEQUENCE cirislens.trace_access_log_id_seq TO cirislens;

-- ============================================================================
-- SECTION 4: Partner Registry
-- ============================================================================

CREATE TABLE IF NOT EXISTS cirislens.partners (
    id VARCHAR(100) PRIMARY KEY,        -- partner_abc
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Access configuration
    agent_scope TEXT[] DEFAULT '{}',    -- agent_id_hashes they own
    api_key_hash VARCHAR(64),           -- hashed API key

    -- Status
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE cirislens.partners IS 'Registry of partners with repository access';

-- Grant permissions
GRANT ALL PRIVILEGES ON cirislens.partners TO cirislens;

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
    AND column_name IN ('public_sample', 'partner_access', 'access_updated_at', 'access_updated_by');

    RAISE NOTICE 'Trace Repository Access Migration Complete:';
    RAISE NOTICE '  - New columns added to covenant_traces: %', col_count;
    RAISE NOTICE '  - New tables: trace_access_log, partners';
    RAISE NOTICE '  - Access levels: full (all), partner (scoped), public (samples)';
END $$;
