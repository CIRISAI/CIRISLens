-- CIRIS Trace Levels and IDMA Support
-- Migration 012: Add trace level tracking and IDMA denormalized fields
-- Reference: CIRISAgent ciris_adapters/ciris_covenant_metrics/README.md

-- ============================================================================
-- SECTION 1: Trace Level and Correlation Metadata for Batches
-- Reference: Early Warning System correlation analysis
-- ============================================================================

-- Add trace_level to covenant_trace_batches
ALTER TABLE cirislens.covenant_trace_batches
ADD COLUMN IF NOT EXISTS trace_level VARCHAR(20) DEFAULT 'generic';

-- Add correlation_metadata for Early Warning System
ALTER TABLE cirislens.covenant_trace_batches
ADD COLUMN IF NOT EXISTS correlation_metadata JSONB;

COMMENT ON COLUMN cirislens.covenant_trace_batches.trace_level IS
    'Trace detail level: generic (scores only), detailed (+ identifiers), full_traces (+ reasoning)';

COMMENT ON COLUMN cirislens.covenant_trace_batches.correlation_metadata IS
    'Optional Early Warning metadata: deployment_region, deployment_type, agent_role, agent_template';

-- ============================================================================
-- SECTION 2: IDMA (Intuition DMA) Denormalized Fields
-- Reference: Coherence Collapse Analysis (CCA) - Covenant Section II, Chapter 5
-- ============================================================================

-- Add IDMA fields to covenant_traces for Coherence Ratchet analysis
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS idma_k_eff NUMERIC(5,2);

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS idma_correlation_risk NUMERIC(5,4);

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS idma_fragility_flag BOOLEAN;

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS idma_phase VARCHAR(20);

COMMENT ON COLUMN cirislens.covenant_traces.idma_k_eff IS
    'Effective number of independent sources: k_eff = k / (1 + ρ(k-1)). Values < 2 indicate fragile reasoning.';

COMMENT ON COLUMN cirislens.covenant_traces.idma_correlation_risk IS
    'Correlation coefficient (ρ) between information sources. High values reduce k_eff.';

COMMENT ON COLUMN cirislens.covenant_traces.idma_fragility_flag IS
    'True if k_eff < 2, indicating dangerous single-source dependence.';

COMMENT ON COLUMN cirislens.covenant_traces.idma_phase IS
    'IDMA assessment phase: nascent, emerging, healthy, or fragile.';

-- ============================================================================
-- SECTION 3: Add trace_level to individual traces
-- Reference: Track what level of detail is available per trace
-- ============================================================================

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS trace_level VARCHAR(20) DEFAULT 'generic';

COMMENT ON COLUMN cirislens.covenant_traces.trace_level IS
    'Detail level of this trace: generic, detailed, or full_traces';

-- ============================================================================
-- SECTION 4: Indexes for IDMA Queries
-- ============================================================================

-- Index for fragility analysis
CREATE INDEX IF NOT EXISTS idx_covenant_traces_idma_fragility
ON cirislens.covenant_traces (idma_fragility_flag, timestamp DESC)
WHERE idma_fragility_flag = TRUE;

-- Index for k_eff analysis
CREATE INDEX IF NOT EXISTS idx_covenant_traces_idma_k_eff
ON cirislens.covenant_traces (idma_k_eff, timestamp DESC)
WHERE idma_k_eff IS NOT NULL;

-- Index for trace level filtering
CREATE INDEX IF NOT EXISTS idx_covenant_traces_trace_level
ON cirislens.covenant_traces (trace_level, timestamp DESC);

-- Index for correlation metadata analysis
CREATE INDEX IF NOT EXISTS idx_covenant_trace_batches_correlation
ON cirislens.covenant_trace_batches USING GIN (correlation_metadata)
WHERE correlation_metadata IS NOT NULL;

-- ============================================================================
-- SECTION 5: Verification
-- ============================================================================

DO $$
DECLARE
    new_columns INTEGER;
BEGIN
    SELECT COUNT(*) INTO new_columns
    FROM information_schema.columns
    WHERE table_schema = 'cirislens'
      AND table_name IN ('covenant_traces', 'covenant_trace_batches')
      AND column_name IN (
          'trace_level', 'correlation_metadata',
          'idma_k_eff', 'idma_correlation_risk', 'idma_fragility_flag', 'idma_phase'
      );

    RAISE NOTICE 'Trace Levels & IDMA Migration Complete:';
    RAISE NOTICE '  - New columns added: %', new_columns;
    RAISE NOTICE '  - covenant_trace_batches: trace_level, correlation_metadata';
    RAISE NOTICE '  - covenant_traces: trace_level, idma_k_eff, idma_correlation_risk, idma_fragility_flag, idma_phase';
    RAISE NOTICE '  - IDMA k_eff formula: k / (1 + ρ(k-1)) where k=sources, ρ=correlation';
    RAISE NOTICE '  - Fragility threshold: k_eff < 2 indicates single-source dependence';
END $$;
