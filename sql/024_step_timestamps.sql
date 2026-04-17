-- Migration 024: Add step timestamps for pipeline timing analysis
--
-- Each trace goes through a pipeline of steps, each with its own timestamp:
-- THOUGHT_START -> SNAPSHOT_AND_CONTEXT -> DMA_RESULTS -> ASPDMA -> CONSCIENCE -> ACTION
--
-- These timestamps enable timeline visualization and performance analysis.

-- Add step timestamp columns to accord_traces
ALTER TABLE cirislens.accord_traces
    ADD COLUMN IF NOT EXISTS thought_start_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS snapshot_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dma_results_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS aspdma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS idma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tsaspdma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS conscience_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS action_result_at TIMESTAMPTZ;

-- Add same columns to mock table
ALTER TABLE cirislens.accord_traces_mock
    ADD COLUMN IF NOT EXISTS thought_start_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS snapshot_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dma_results_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS aspdma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS idma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tsaspdma_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS conscience_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS action_result_at TIMESTAMPTZ;

-- Add comments
COMMENT ON COLUMN cirislens.accord_traces.thought_start_at IS 'Timestamp when THOUGHT_START event occurred';
COMMENT ON COLUMN cirislens.accord_traces.snapshot_at IS 'Timestamp when SNAPSHOT_AND_CONTEXT event occurred';
COMMENT ON COLUMN cirislens.accord_traces.dma_results_at IS 'Timestamp when DMA_RESULTS event occurred (CSDMA/DSDMA/PDMA complete)';
COMMENT ON COLUMN cirislens.accord_traces.aspdma_at IS 'Timestamp when ASPDMA_RESULT event occurred (action selection)';
COMMENT ON COLUMN cirislens.accord_traces.idma_at IS 'Timestamp when IDMA_RESULT event occurred (epistemic analysis)';
COMMENT ON COLUMN cirislens.accord_traces.tsaspdma_at IS 'Timestamp when TSASPDMA_RESULT event occurred (tool-specific action)';
COMMENT ON COLUMN cirislens.accord_traces.conscience_at IS 'Timestamp when CONSCIENCE_RESULT event occurred';
COMMENT ON COLUMN cirislens.accord_traces.action_result_at IS 'Timestamp when ACTION_RESULT event occurred (execution complete)';

-- Index for pipeline timing queries
CREATE INDEX IF NOT EXISTS idx_accord_traces_pipeline_timing
ON cirislens.accord_traces(thought_start_at, action_result_at)
WHERE thought_start_at IS NOT NULL AND action_result_at IS NOT NULL;

-- Verification
DO $$
BEGIN
    RAISE NOTICE 'Migration 024: Step timestamps added';
    RAISE NOTICE '  - New columns: thought_start_at, snapshot_at, dma_results_at, aspdma_at, idma_at, tsaspdma_at, conscience_at, action_result_at';
    RAISE NOTICE '  - Enables pipeline timeline visualization';
END $$;
