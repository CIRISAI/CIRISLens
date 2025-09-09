-- Add error tracking table for better observability
CREATE TABLE IF NOT EXISTS collection_errors (
    id SERIAL PRIMARY KEY,
    source VARCHAR(255) NOT NULL,           -- e.g., 'manager_collector:primary', 'otlp_collector'  
    error_type VARCHAR(100) NOT NULL,       -- e.g., 'DISCOVERY_FAILURE', 'NETWORK_ERROR'
    error_message TEXT NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP WITH TIME ZONE,  -- When error was resolved
    
    -- Indexes for querying
    INDEX idx_collection_errors_source_time (source, occurred_at),
    INDEX idx_collection_errors_type (error_type),
    INDEX idx_collection_errors_unresolved (occurred_at) WHERE resolved_at IS NULL
);

-- Add error tracking column to managers table
ALTER TABLE managers 
ADD COLUMN IF NOT EXISTS last_error TEXT,
ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMP WITH TIME ZONE;

-- Function to auto-resolve errors when collection succeeds
CREATE OR REPLACE FUNCTION mark_errors_resolved() RETURNS TRIGGER AS $$
BEGIN
    -- When agents are successfully discovered, mark related errors as resolved
    IF TG_TABLE_NAME = 'discovered_agents' AND TG_OP = 'INSERT' THEN
        UPDATE collection_errors 
        SET resolved_at = CURRENT_TIMESTAMP 
        WHERE source LIKE 'manager_collector:%' 
          AND error_type = 'DISCOVERY_FAILURE'
          AND resolved_at IS NULL
          AND occurred_at < NEW.last_seen;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-resolve errors on successful discovery
DROP TRIGGER IF EXISTS resolve_discovery_errors ON discovered_agents;
CREATE TRIGGER resolve_discovery_errors
    AFTER INSERT OR UPDATE ON discovered_agents
    FOR EACH ROW
    EXECUTE FUNCTION mark_errors_resolved();