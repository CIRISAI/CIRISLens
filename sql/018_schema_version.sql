-- Migration 018: Add schema_version column for CIRIS Scoring eligibility tracking

-- Add schema_version to production traces
ALTER TABLE cirislens.covenant_traces
ADD COLUMN IF NOT EXISTS schema_version VARCHAR(10);

-- Add schema_version to mock traces
ALTER TABLE cirislens.covenant_traces_mock
ADD COLUMN IF NOT EXISTS schema_version VARCHAR(10);

-- Index for querying traces by schema version (scoring eligibility)
CREATE INDEX IF NOT EXISTS idx_traces_schema_version
ON cirislens.covenant_traces (schema_version)
WHERE schema_version IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_mock_traces_schema_version
ON cirislens.covenant_traces_mock (schema_version)
WHERE schema_version IS NOT NULL;

-- Comment
COMMENT ON COLUMN cirislens.covenant_traces.schema_version IS 'Detected schema version (1.8, 1.9, 1.9.1) - 1.9.1+ required for CIRIS Scoring';
COMMENT ON COLUMN cirislens.covenant_traces_mock.schema_version IS 'Detected schema version (1.8, 1.9, 1.9.1) - 1.9.1+ required for CIRIS Scoring';
