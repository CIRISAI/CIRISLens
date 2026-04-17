-- Migration 025: Add observation weight fields for timeline visualization
--
-- These numeric fields capture the "weight" of each observation without
-- revealing any text content - safe for privacy-preserving analysis.
--
-- Fields:
--   memory_count: How many memories were retrieved for context
--   context_tokens: Token count of the full context window
--   conversation_turns: Number of turns in conversation history
--   alternatives_considered: How many actions ASPDMA evaluated
--   conscience_checks_count: Number of ethical checks run

-- Add observation weight columns to accord_traces
ALTER TABLE cirislens.accord_traces
    ADD COLUMN IF NOT EXISTS memory_count INTEGER,
    ADD COLUMN IF NOT EXISTS context_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS conversation_turns INTEGER,
    ADD COLUMN IF NOT EXISTS alternatives_considered INTEGER,
    ADD COLUMN IF NOT EXISTS conscience_checks_count INTEGER;

-- Add same columns to mock table
ALTER TABLE cirislens.accord_traces_mock
    ADD COLUMN IF NOT EXISTS memory_count INTEGER,
    ADD COLUMN IF NOT EXISTS context_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS conversation_turns INTEGER,
    ADD COLUMN IF NOT EXISTS alternatives_considered INTEGER,
    ADD COLUMN IF NOT EXISTS conscience_checks_count INTEGER;

-- Add comments
COMMENT ON COLUMN cirislens.accord_traces.memory_count IS 'Count of memories retrieved for context (from SNAPSHOT_AND_CONTEXT)';
COMMENT ON COLUMN cirislens.accord_traces.context_tokens IS 'Token count of full context window (from SNAPSHOT_AND_CONTEXT)';
COMMENT ON COLUMN cirislens.accord_traces.conversation_turns IS 'Number of conversation turns in history (from SNAPSHOT_AND_CONTEXT)';
COMMENT ON COLUMN cirislens.accord_traces.alternatives_considered IS 'Number of actions evaluated by ASPDMA';
COMMENT ON COLUMN cirislens.accord_traces.conscience_checks_count IS 'Number of ethical checks run by conscience';

-- Index for observation complexity queries
CREATE INDEX IF NOT EXISTS idx_accord_traces_observation_weight
ON cirislens.accord_traces(memory_count, context_tokens)
WHERE memory_count IS NOT NULL;

-- Verification
DO $$
BEGIN
    RAISE NOTICE 'Migration 025: Observation weight fields added';
    RAISE NOTICE '  - memory_count, context_tokens, conversation_turns';
    RAISE NOTICE '  - alternatives_considered, conscience_checks_count';
END $$;
