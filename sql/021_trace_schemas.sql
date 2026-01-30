-- Migration 021: Add trace schema registry tables for DB-driven schema validation
-- This replaces the hardcoded switch-case schema detection in trace_schema_registry.py

-- =============================================================================
-- Schema Registry Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS cirislens.trace_schemas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version VARCHAR(20) NOT NULL UNIQUE,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'current',  -- current, supported, deprecated
    definition JSONB NOT NULL,                       -- Full schema definition JSON
    signature_event_types TEXT[],                    -- Event types that identify this schema
    required_event_types TEXT[],                     -- Events that must be present
    optional_event_types TEXT[],                     -- Events that may be present
    source_url TEXT,                                 -- URL where schema was fetched from
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    synced_at TIMESTAMPTZ                            -- Last sync from remote repository
);

-- Index for status queries (loading active schemas)
CREATE INDEX IF NOT EXISTS idx_trace_schemas_status
ON cirislens.trace_schemas (status);

-- =============================================================================
-- Field Extraction Rules Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS cirislens.trace_schema_fields (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version VARCHAR(20) NOT NULL REFERENCES cirislens.trace_schemas(version) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    json_path TEXT NOT NULL,                         -- e.g., "csdma.plausibility_score"
    data_type VARCHAR(20) NOT NULL,                  -- string, float, int, boolean, json, timestamp
    required BOOLEAN DEFAULT FALSE,
    db_column VARCHAR(100),                          -- Target column in covenant_traces
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (schema_version, event_type, field_name)
);

-- Index for fast lookup during extraction
CREATE INDEX IF NOT EXISTS idx_schema_fields_lookup
ON cirislens.trace_schema_fields (schema_version, event_type);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE cirislens.trace_schemas IS 'Registry of trace schema versions for DB-driven validation';
COMMENT ON COLUMN cirislens.trace_schemas.version IS 'Schema version identifier (e.g., 1.8, 1.9.3, connectivity)';
COMMENT ON COLUMN cirislens.trace_schemas.status IS 'Schema status: current (recommended), supported (accepted), deprecated (logged warning)';
COMMENT ON COLUMN cirislens.trace_schemas.definition IS 'Full schema definition JSON including field extractions';
COMMENT ON COLUMN cirislens.trace_schemas.signature_event_types IS 'Event types that uniquely identify this schema version';
COMMENT ON COLUMN cirislens.trace_schemas.required_event_types IS 'Event types that must be present for a valid trace';
COMMENT ON COLUMN cirislens.trace_schemas.optional_event_types IS 'Event types that may be present but are not required';

COMMENT ON TABLE cirislens.trace_schema_fields IS 'Field extraction rules for each schema/event_type combination';
COMMENT ON COLUMN cirislens.trace_schema_fields.json_path IS 'Dot-notation path to field in event data (e.g., csdma.plausibility_score)';
COMMENT ON COLUMN cirislens.trace_schema_fields.data_type IS 'Target data type: string, float, int, boolean, json, timestamp';
COMMENT ON COLUMN cirislens.trace_schema_fields.db_column IS 'Target column name in covenant_traces table';

-- =============================================================================
-- Seed Initial Schema Data
-- =============================================================================

-- Insert V1.8 schema (deprecated)
INSERT INTO cirislens.trace_schemas (version, description, status, definition, signature_event_types, required_event_types, optional_event_types)
VALUES (
    '1.8',
    'Legacy trace format without SNAPSHOT_AND_CONTEXT or separate IDMA',
    'deprecated',
    '{
        "version": "1.8",
        "description": "Legacy trace format without SNAPSHOT_AND_CONTEXT or separate IDMA"
    }'::jsonb,
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'ACTION_RESULT'],
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'ACTION_RESULT'],
    NULL
) ON CONFLICT (version) DO NOTHING;

-- Insert V1.9 schema (deprecated)
INSERT INTO cirislens.trace_schemas (version, description, status, definition, signature_event_types, required_event_types, optional_event_types)
VALUES (
    '1.9',
    'Trace format with ASPDMA and CONSCIENCE but no SNAPSHOT',
    'deprecated',
    '{
        "version": "1.9",
        "description": "Trace format with ASPDMA and CONSCIENCE but no SNAPSHOT"
    }'::jsonb,
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'ASPDMA_RESULT', 'CONSCIENCE_RESULT', 'ACTION_RESULT'],
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'ASPDMA_RESULT', 'CONSCIENCE_RESULT', 'ACTION_RESULT'],
    NULL
) ON CONFLICT (version) DO NOTHING;

-- Insert V1.9.1 schema (supported)
INSERT INTO cirislens.trace_schemas (version, description, status, definition, signature_event_types, required_event_types, optional_event_types)
VALUES (
    '1.9.1',
    'Full trace format with SNAPSHOT_AND_CONTEXT, IDMA embedded in DMA_RESULTS',
    'supported',
    '{
        "version": "1.9.1",
        "description": "Full trace format with SNAPSHOT_AND_CONTEXT, IDMA embedded in DMA_RESULTS"
    }'::jsonb,
    ARRAY['THOUGHT_START', 'SNAPSHOT_AND_CONTEXT', 'DMA_RESULTS', 'ASPDMA_RESULT', 'CONSCIENCE_RESULT', 'ACTION_RESULT'],
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'ASPDMA_RESULT', 'ACTION_RESULT'],
    ARRAY['SNAPSHOT_AND_CONTEXT', 'CONSCIENCE_RESULT']
) ON CONFLICT (version) DO NOTHING;

-- Insert V1.9.3 schema (current)
INSERT INTO cirislens.trace_schemas (version, description, status, definition, signature_event_types, required_event_types, optional_event_types)
VALUES (
    '1.9.3',
    'Full trace format with separate IDMA_RESULT event and TSASPDMA support',
    'current',
    '{
        "version": "1.9.3",
        "description": "Full trace format with separate IDMA_RESULT event and TSASPDMA support"
    }'::jsonb,
    ARRAY['THOUGHT_START', 'SNAPSHOT_AND_CONTEXT', 'DMA_RESULTS', 'IDMA_RESULT', 'ASPDMA_RESULT', 'CONSCIENCE_RESULT', 'ACTION_RESULT'],
    ARRAY['THOUGHT_START', 'DMA_RESULTS', 'IDMA_RESULT', 'ASPDMA_RESULT', 'ACTION_RESULT'],
    ARRAY['SNAPSHOT_AND_CONTEXT', 'CONSCIENCE_RESULT', 'TSASPDMA_RESULT']
) ON CONFLICT (version) DO NOTHING;

-- Insert connectivity schema (current)
INSERT INTO cirislens.trace_schemas (version, description, status, definition, signature_event_types, required_event_types, optional_event_types)
VALUES (
    'connectivity',
    'Agent startup/shutdown connectivity events',
    'current',
    '{
        "version": "connectivity",
        "description": "Agent startup/shutdown connectivity events",
        "special_handling": true
    }'::jsonb,
    ARRAY['startup', 'shutdown'],
    NULL,
    NULL
) ON CONFLICT (version) DO NOTHING;

-- =============================================================================
-- Seed Field Extraction Rules for V1.9.3 (current schema)
-- =============================================================================

-- THOUGHT_START fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'THOUGHT_START', 'thought_id', 'thought_id', 'string', true, 'thought_id', 'Unique identifier for this thought'),
    ('1.9.3', 'THOUGHT_START', 'thought_type', 'thought_type', 'string', false, 'thought_type', 'Type of thought (e.g., action, reflection)'),
    ('1.9.3', 'THOUGHT_START', 'thought_depth', 'depth', 'int', false, 'thought_depth', 'Recursion depth of thought'),
    ('1.9.3', 'THOUGHT_START', 'task_id', 'task_id', 'string', false, 'task_id', 'Associated task identifier'),
    ('1.9.3', 'THOUGHT_START', 'task_description', 'task_description', 'string', false, 'task_description', 'Description of the task'),
    ('1.9.3', 'THOUGHT_START', 'started_at', 'started_at', 'timestamp', false, 'started_at', 'Timestamp when thought started')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- SNAPSHOT_AND_CONTEXT fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'SNAPSHOT_AND_CONTEXT', 'agent_name', 'system_snapshot.agent_profile.name', 'string', false, 'agent_name', 'Name of the agent'),
    ('1.9.3', 'SNAPSHOT_AND_CONTEXT', 'cognitive_state', 'system_snapshot.cognitive_state', 'string', false, 'cognitive_state', 'Current cognitive state'),
    ('1.9.3', 'SNAPSHOT_AND_CONTEXT', 'initial_context', 'initial_context', 'json', false, 'initial_context', 'Initial context data'),
    ('1.9.3', 'SNAPSHOT_AND_CONTEXT', 'system_snapshot', 'system_snapshot', 'json', false, 'system_snapshot', 'Full system snapshot'),
    ('1.9.3', 'SNAPSHOT_AND_CONTEXT', 'gathered_context', 'gathered_context', 'json', false, 'gathered_context', 'Gathered context data')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- DMA_RESULTS fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'DMA_RESULTS', 'csdma_plausibility', 'csdma.plausibility_score', 'float', false, 'csdma_plausibility', 'CSDMA plausibility score'),
    ('1.9.3', 'DMA_RESULTS', 'csdma_confidence', 'csdma.confidence', 'float', false, 'csdma_confidence', 'CSDMA confidence level'),
    ('1.9.3', 'DMA_RESULTS', 'csdma_reasoning', 'csdma.reasoning', 'string', false, 'csdma_reasoning', 'CSDMA reasoning text'),
    ('1.9.3', 'DMA_RESULTS', 'dsdma_alignment', 'dsdma.domain_alignment', 'float', false, 'dsdma_alignment', 'DSDMA domain alignment score'),
    ('1.9.3', 'DMA_RESULTS', 'dsdma_confidence', 'dsdma.confidence', 'float', false, 'dsdma_confidence', 'DSDMA confidence level'),
    ('1.9.3', 'DMA_RESULTS', 'dsdma_reasoning', 'dsdma.reasoning', 'string', false, 'dsdma_reasoning', 'DSDMA reasoning text'),
    ('1.9.3', 'DMA_RESULTS', 'pdma_stakeholder_score', 'pdma.stakeholder_impact_score', 'float', false, 'pdma_stakeholder_score', 'PDMA stakeholder impact score'),
    ('1.9.3', 'DMA_RESULTS', 'pdma_conflict_detected', 'pdma.conflict_detected', 'boolean', false, 'pdma_conflict_detected', 'Whether PDMA detected a conflict'),
    ('1.9.3', 'DMA_RESULTS', 'pdma_reasoning', 'pdma.reasoning', 'string', false, 'pdma_reasoning', 'PDMA reasoning text'),
    ('1.9.3', 'DMA_RESULTS', 'dma_results', '', 'json', false, 'dma_results', 'Full DMA results object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- IDMA_RESULT fields (separate event in V1.9.3)
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'IDMA_RESULT', 'idma_k_eff', 'k_eff', 'float', false, 'idma_k_eff', 'IDMA effective correlation coefficient'),
    ('1.9.3', 'IDMA_RESULT', 'idma_correlation_risk', 'correlation_risk', 'float', false, 'idma_correlation_risk', 'IDMA correlation risk score'),
    ('1.9.3', 'IDMA_RESULT', 'idma_fragility_flag', 'fragility_flag', 'boolean', false, 'idma_fragility_flag', 'IDMA fragility flag'),
    ('1.9.3', 'IDMA_RESULT', 'idma_phase', 'phase', 'string', false, 'idma_phase', 'IDMA phase identifier'),
    ('1.9.3', 'IDMA_RESULT', 'idma_confidence', 'confidence', 'float', false, 'idma_confidence', 'IDMA confidence level'),
    ('1.9.3', 'IDMA_RESULT', 'idma_reasoning', 'reasoning', 'string', false, 'idma_reasoning', 'IDMA reasoning text'),
    ('1.9.3', 'IDMA_RESULT', 'idma_result', '', 'json', false, 'idma_result', 'Full IDMA result object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- ASPDMA_RESULT fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'ASPDMA_RESULT', 'selected_action', 'selected_action', 'string', false, 'selected_action', 'Action selected by ASPDMA'),
    ('1.9.3', 'ASPDMA_RESULT', 'action_rationale', 'rationale', 'string', false, 'action_rationale', 'Rationale for selected action'),
    ('1.9.3', 'ASPDMA_RESULT', 'aspdma_confidence', 'confidence', 'float', false, 'aspdma_confidence', 'ASPDMA confidence level'),
    ('1.9.3', 'ASPDMA_RESULT', 'aspdma_result', '', 'json', false, 'aspdma_result', 'Full ASPDMA result object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- TSASPDMA_RESULT fields (tool-specific ASPDMA in V1.9.3)
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'TSASPDMA_RESULT', 'tool_name', 'final_tool_name', 'string', false, 'tool_name', 'Name of the tool being evaluated'),
    ('1.9.3', 'TSASPDMA_RESULT', 'tool_parameters', 'final_parameters', 'json', false, 'tool_parameters', 'Tool parameters'),
    ('1.9.3', 'TSASPDMA_RESULT', 'tsaspdma_reasoning', 'tsaspdma_rationale', 'string', false, 'tsaspdma_reasoning', 'TSASPDMA reasoning for approval/rejection'),
    ('1.9.3', 'TSASPDMA_RESULT', 'tsaspdma_approved', 'final_action', 'string', false, 'tsaspdma_approved', 'Whether tool use was approved (final_action=tool)'),
    ('1.9.3', 'TSASPDMA_RESULT', 'tsaspdma_result', '', 'json', false, 'tsaspdma_result', 'Full TSASPDMA result object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- CONSCIENCE_RESULT fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'CONSCIENCE_RESULT', 'conscience_passed', 'passed', 'boolean', false, 'conscience_passed', 'Whether conscience check passed'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'conscience_override', 'override', 'boolean', false, 'conscience_override', 'Whether conscience was overridden'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'conscience_override_reason', 'override_reason', 'string', false, 'conscience_override_reason', 'Reason for conscience override'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'epistemic_humility', 'ethical_faculties.epistemic_humility', 'boolean', false, 'epistemic_humility', 'Epistemic humility faculty'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'entropy_awareness', 'ethical_faculties.entropy_awareness', 'boolean', false, 'entropy_awareness', 'Entropy awareness faculty'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'coherence_alignment', 'ethical_faculties.coherence_alignment', 'boolean', false, 'coherence_alignment', 'Coherence alignment faculty'),
    ('1.9.3', 'CONSCIENCE_RESULT', 'conscience_result', '', 'json', false, 'conscience_result', 'Full conscience result object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- ACTION_RESULT fields
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.3', 'ACTION_RESULT', 'action_success', 'success', 'boolean', false, 'action_success', 'Whether action succeeded'),
    ('1.9.3', 'ACTION_RESULT', 'action_type', 'action_type', 'string', false, 'action_type', 'Type of action taken'),
    ('1.9.3', 'ACTION_RESULT', 'tokens_used', 'tokens_used', 'int', false, 'tokens_used', 'Number of tokens used'),
    ('1.9.3', 'ACTION_RESULT', 'cost_usd', 'cost_usd', 'float', false, 'cost_usd', 'Cost in USD'),
    ('1.9.3', 'ACTION_RESULT', 'models_used', 'models_used', 'json', false, 'models_used', 'Models used for this action'),
    ('1.9.3', 'ACTION_RESULT', 'api_bases_used', 'api_bases_used', 'json', false, 'api_bases_used', 'API bases used'),
    ('1.9.3', 'ACTION_RESULT', 'completed_at', 'completed_at', 'timestamp', false, 'completed_at', 'Timestamp when action completed'),
    ('1.9.3', 'ACTION_RESULT', 'positive_moment', 'positive_moment', 'boolean', false, 'positive_moment', 'Whether this was a positive moment'),
    ('1.9.3', 'ACTION_RESULT', 'action_result', '', 'json', false, 'action_result', 'Full action result object')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;

-- =============================================================================
-- Copy V1.9.3 field rules to V1.9.1 (with adjustments)
-- =============================================================================

-- V1.9.1 has IDMA embedded in DMA_RESULTS, not as separate event
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
SELECT '1.9.1', event_type, field_name, json_path, data_type, required, db_column, description
FROM cirislens.trace_schema_fields
WHERE schema_version = '1.9.3' AND event_type != 'IDMA_RESULT' AND event_type != 'TSASPDMA_RESULT'
ON CONFLICT (schema_version, event_type, field_name) DO NOTHING;

-- Add IDMA fields to DMA_RESULTS for V1.9.1 (embedded IDMA)
INSERT INTO cirislens.trace_schema_fields (schema_version, event_type, field_name, json_path, data_type, required, db_column, description)
VALUES
    ('1.9.1', 'DMA_RESULTS', 'idma_k_eff', 'idma.k_eff', 'float', false, 'idma_k_eff', 'IDMA effective correlation coefficient'),
    ('1.9.1', 'DMA_RESULTS', 'idma_correlation_risk', 'idma.correlation_risk', 'float', false, 'idma_correlation_risk', 'IDMA correlation risk score'),
    ('1.9.1', 'DMA_RESULTS', 'idma_fragility_flag', 'idma.fragility_flag', 'boolean', false, 'idma_fragility_flag', 'IDMA fragility flag'),
    ('1.9.1', 'DMA_RESULTS', 'idma_phase', 'idma.phase', 'string', false, 'idma_phase', 'IDMA phase identifier')
ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
    json_path = EXCLUDED.json_path,
    data_type = EXCLUDED.data_type,
    db_column = EXCLUDED.db_column;
