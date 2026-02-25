-- Migration 022: Rename covenant tables to accord tables
--
-- This migration renames all covenant_* tables to accord_* and creates
-- backward-compatible views with the old names.
--
-- The "Accord" naming replaces "Covenant" across the CIRIS ecosystem.

-- ============================================================================
-- Step 1: Rename tables
-- ============================================================================

-- Rename covenant_traces to accord_traces
ALTER TABLE IF EXISTS cirislens.covenant_traces
    RENAME TO accord_traces;

-- Rename covenant_traces_mock to accord_traces_mock
ALTER TABLE IF EXISTS cirislens.covenant_traces_mock
    RENAME TO accord_traces_mock;

-- Rename covenant_trace_batches to accord_trace_batches
ALTER TABLE IF EXISTS cirislens.covenant_trace_batches
    RENAME TO accord_trace_batches;

-- Rename covenant_trace_metrics to accord_trace_metrics
ALTER TABLE IF EXISTS cirislens.covenant_trace_metrics
    RENAME TO accord_trace_metrics;

-- Rename covenant_public_keys to accord_public_keys
ALTER TABLE IF EXISTS cirislens.covenant_public_keys
    RENAME TO accord_public_keys;

-- ============================================================================
-- Step 2: Rename sequences (PostgreSQL auto-renames with table, but be explicit)
-- ============================================================================

-- Sequences are typically named tablename_columnname_seq
-- These should auto-rename with ALTER TABLE, but we ensure consistency

-- ============================================================================
-- Step 3: Rename indexes
-- ============================================================================

-- Rename indexes that include "covenant" in their name
DO $$
DECLARE
    idx RECORD;
    new_name TEXT;
BEGIN
    FOR idx IN
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'cirislens'
        AND indexname LIKE '%covenant%'
    LOOP
        new_name := REPLACE(idx.indexname, 'covenant', 'accord');
        EXECUTE format('ALTER INDEX IF EXISTS cirislens.%I RENAME TO %I',
                      idx.indexname, new_name);
        RAISE NOTICE 'Renamed index % to %', idx.indexname, new_name;
    END LOOP;
END $$;

-- ============================================================================
-- Step 4: Create backward-compatible views
-- ============================================================================

-- View for covenant_traces (deprecated, use accord_traces)
CREATE OR REPLACE VIEW cirislens.covenant_traces AS
SELECT * FROM cirislens.accord_traces;
COMMENT ON VIEW cirislens.covenant_traces IS
    'DEPRECATED: Backward-compatible view. Use accord_traces instead.';

-- View for covenant_traces_mock (deprecated, use accord_traces_mock)
CREATE OR REPLACE VIEW cirislens.covenant_traces_mock AS
SELECT * FROM cirislens.accord_traces_mock;
COMMENT ON VIEW cirislens.covenant_traces_mock IS
    'DEPRECATED: Backward-compatible view. Use accord_traces_mock instead.';

-- View for covenant_trace_batches (deprecated, use accord_trace_batches)
CREATE OR REPLACE VIEW cirislens.covenant_trace_batches AS
SELECT * FROM cirislens.accord_trace_batches;
COMMENT ON VIEW cirislens.covenant_trace_batches IS
    'DEPRECATED: Backward-compatible view. Use accord_trace_batches instead.';

-- View for covenant_trace_metrics (deprecated, use accord_trace_metrics)
CREATE OR REPLACE VIEW cirislens.covenant_trace_metrics AS
SELECT * FROM cirislens.accord_trace_metrics;
COMMENT ON VIEW cirislens.covenant_trace_metrics IS
    'DEPRECATED: Backward-compatible view. Use accord_trace_metrics instead.';

-- View for covenant_public_keys (deprecated, use accord_public_keys)
CREATE OR REPLACE VIEW cirislens.covenant_public_keys AS
SELECT * FROM cirislens.accord_public_keys;
COMMENT ON VIEW cirislens.covenant_public_keys IS
    'DEPRECATED: Backward-compatible view. Use accord_public_keys instead.';

-- ============================================================================
-- Step 5: Update foreign key references (if any point to renamed tables)
-- ============================================================================

-- The case_law_candidates table has FK to covenant_traces
-- Update constraint to point to accord_traces
DO $$
BEGIN
    -- Drop old FK if exists
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_trace'
        AND table_schema = 'cirislens'
        AND table_name = 'case_law_candidates'
    ) THEN
        ALTER TABLE cirislens.case_law_candidates DROP CONSTRAINT fk_trace;

        -- Re-add with new table name
        ALTER TABLE cirislens.case_law_candidates
            ADD CONSTRAINT fk_trace
            FOREIGN KEY (trace_id, trace_timestamp)
            REFERENCES cirislens.accord_traces(trace_id, timestamp)
            ON DELETE CASCADE;

        RAISE NOTICE 'Updated FK constraint fk_trace to reference accord_traces';
    END IF;
END $$;

-- ============================================================================
-- Step 6: Record migration
-- ============================================================================

INSERT INTO cirislens.schema_migrations (version, description, applied_at)
VALUES ('022', 'Rename covenant tables to accord tables with backward-compatible views', NOW())
ON CONFLICT (version) DO NOTHING;
