-- Migration 017: Add positive moment and ASPDMA fields for CIRIS scoring
-- These fields support the S factor (Sustained Coherence) enhancement

-- Add columns to production traces table
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS has_positive_moment BOOLEAN,
ADD COLUMN IF NOT EXISTS has_execution_error BOOLEAN,
ADD COLUMN IF NOT EXISTS execution_time_ms NUMERIC(10,3),
ADD COLUMN IF NOT EXISTS selection_confidence NUMERIC(3,2),
ADD COLUMN IF NOT EXISTS is_recursive BOOLEAN,
ADD COLUMN IF NOT EXISTS follow_up_thought_id VARCHAR(128),
ADD COLUMN IF NOT EXISTS api_bases_used TEXT[];

-- Add columns to mock traces table
ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS has_positive_moment BOOLEAN,
ADD COLUMN IF NOT EXISTS has_execution_error BOOLEAN,
ADD COLUMN IF NOT EXISTS execution_time_ms NUMERIC(10,3),
ADD COLUMN IF NOT EXISTS selection_confidence NUMERIC(3,2),
ADD COLUMN IF NOT EXISTS is_recursive BOOLEAN,
ADD COLUMN IF NOT EXISTS follow_up_thought_id VARCHAR(128),
ADD COLUMN IF NOT EXISTS api_bases_used TEXT[];

-- Index for positive moment queries (scoring)
CREATE INDEX IF NOT EXISTS idx_traces_positive_moment
ON cirislens.covenant_traces (agent_name, has_positive_moment)
WHERE has_positive_moment = true;

CREATE INDEX IF NOT EXISTS idx_mock_traces_positive_moment
ON cirislens.covenant_traces_mock (agent_name, has_positive_moment)
WHERE has_positive_moment = true;

-- Comments
COMMENT ON COLUMN cirislens.covenant_traces.has_positive_moment IS 'Agent expressed gratitude or positive engagement - key for S factor scoring';
COMMENT ON COLUMN cirislens.covenant_traces.has_execution_error IS 'Action resulted in execution error';
COMMENT ON COLUMN cirislens.covenant_traces.execution_time_ms IS 'Action execution time in milliseconds';
COMMENT ON COLUMN cirislens.covenant_traces.selection_confidence IS 'ASPDMA selection confidence (0.0-1.0)';
COMMENT ON COLUMN cirislens.covenant_traces.is_recursive IS 'Whether action triggered recursive processing';
COMMENT ON COLUMN cirislens.covenant_traces.follow_up_thought_id IS 'ID of follow-up thought in chain';
COMMENT ON COLUMN cirislens.covenant_traces.api_bases_used IS 'LLM API endpoints used';
