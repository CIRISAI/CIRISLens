-- Migration 019: Add TSASPDMA (Tool-Specific ASPDMA) columns for V1.9.3 schema support
-- Also adds support for IDMA_RESULT as separate event type

-- =============================================================================
-- Production traces table
-- =============================================================================

-- TSASPDMA result JSON (full component data)
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS tsaspdma_result JSONB;

-- Extracted TSASPDMA fields
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS tool_name VARCHAR(255);

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS tool_parameters JSONB;

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS tsaspdma_reasoning TEXT;

ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS tsaspdma_approved BOOLEAN;

-- IDMA result JSON (for V1.9.3 where IDMA is separate event)
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS idma_result JSONB;

-- Index for TOOL action queries
CREATE INDEX IF NOT EXISTS idx_traces_tool_name
ON cirislens.covenant_traces (tool_name, "timestamp" DESC)
WHERE tool_name IS NOT NULL;

-- =============================================================================
-- Mock traces table (same columns)
-- =============================================================================

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS tsaspdma_result JSONB;

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS tool_name VARCHAR(255);

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS tool_parameters JSONB;

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS tsaspdma_reasoning TEXT;

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS tsaspdma_approved BOOLEAN;

ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS idma_result JSONB;

CREATE INDEX IF NOT EXISTS idx_mock_traces_tool_name
ON cirislens.covenant_traces_mock (tool_name)
WHERE tool_name IS NOT NULL;

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON COLUMN cirislens.covenant_traces.tsaspdma_result IS 'Full TSASPDMA_RESULT component JSON (V1.9.3+)';
COMMENT ON COLUMN cirislens.covenant_traces.tool_name IS 'Tool name from TSASPDMA evaluation (e.g., mcp_server_tool_name)';
COMMENT ON COLUMN cirislens.covenant_traces.tool_parameters IS 'Tool parameters evaluated by TSASPDMA';
COMMENT ON COLUMN cirislens.covenant_traces.tsaspdma_reasoning IS 'TSASPDMA reasoning for tool approval/rejection';
COMMENT ON COLUMN cirislens.covenant_traces.tsaspdma_approved IS 'Whether TSASPDMA approved the tool use';
COMMENT ON COLUMN cirislens.covenant_traces.idma_result IS 'Full IDMA_RESULT component JSON (V1.9.3+)';

COMMENT ON COLUMN cirislens.covenant_traces_mock.tsaspdma_result IS 'Full TSASPDMA_RESULT component JSON (V1.9.3+)';
COMMENT ON COLUMN cirislens.covenant_traces_mock.tool_name IS 'Tool name from TSASPDMA evaluation';
COMMENT ON COLUMN cirislens.covenant_traces_mock.tool_parameters IS 'Tool parameters evaluated by TSASPDMA';
COMMENT ON COLUMN cirislens.covenant_traces_mock.tsaspdma_reasoning IS 'TSASPDMA reasoning for tool approval/rejection';
COMMENT ON COLUMN cirislens.covenant_traces_mock.tsaspdma_approved IS 'Whether TSASPDMA approved the tool use';
COMMENT ON COLUMN cirislens.covenant_traces_mock.idma_result IS 'Full IDMA_RESULT component JSON (V1.9.3+)';

-- Update schema version comment
COMMENT ON COLUMN cirislens.covenant_traces.schema_version IS 'Detected schema version (1.8, 1.9, 1.9.1, 1.9.3) - 1.9.1+ required for CIRIS Scoring';
COMMENT ON COLUMN cirislens.covenant_traces_mock.schema_version IS 'Detected schema version (1.8, 1.9, 1.9.1, 1.9.3) - 1.9.1+ required for CIRIS Scoring';
