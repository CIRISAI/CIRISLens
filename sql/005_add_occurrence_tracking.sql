-- Add occurrence tracking to discovered_agents table
-- This allows tracking multiple occurrences of the same agent across different servers

-- Drop old unique constraint that prevented tracking multiple occurrences
ALTER TABLE discovered_agents DROP CONSTRAINT IF EXISTS discovered_agents_manager_id_agent_id_key;

-- Add columns for occurrence and server identification
ALTER TABLE discovered_agents
ADD COLUMN IF NOT EXISTS occurrence_id VARCHAR(50),
ADD COLUMN IF NOT EXISTS server_id VARCHAR(100);

-- Create new unique index that includes occurrence_id
-- This allows same agent_id to exist multiple times with different occurrence_ids
-- e.g., scout-remote-test-dahrb9 can exist as occurrence null (001) and "002"
CREATE UNIQUE INDEX IF NOT EXISTS discovered_agents_unique_occurrence
ON discovered_agents(manager_id, agent_id, COALESCE(occurrence_id, 'default'));

-- Update existing rows to populate occurrence_id and server_id from raw_data
UPDATE discovered_agents
SET
    occurrence_id = raw_data->>'occurrence_id',
    server_id = raw_data->>'server_id'
WHERE occurrence_id IS NULL;
