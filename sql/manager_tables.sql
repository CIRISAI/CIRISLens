-- Manager configuration tables for CIRISLens
-- Stores registered CIRISManager instances to collect telemetry from

CREATE TABLE IF NOT EXISTS managers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    url VARCHAR(512) NOT NULL,
    enabled BOOLEAN DEFAULT true,
    auth_required BOOLEAN DEFAULT false,
    auth_token TEXT,
    description TEXT,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    collection_interval_seconds INTEGER DEFAULT 30,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Manager telemetry collection history
CREATE TABLE IF NOT EXISTS manager_telemetry (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id) ON DELETE CASCADE,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    agent_count INTEGER,
    status VARCHAR(50),
    version VARCHAR(50),
    uptime_seconds INTEGER,
    raw_data JSONB,
    INDEX idx_manager_telemetry_time (manager_id, collected_at DESC)
);

-- Agent discoveries from managers
CREATE TABLE IF NOT EXISTS discovered_agents (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),
    status VARCHAR(50),
    cognitive_state VARCHAR(50),
    version VARCHAR(50),
    codename VARCHAR(255),
    api_port INTEGER,
    health VARCHAR(50),
    template VARCHAR(100),
    deployment VARCHAR(255),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    raw_data JSONB,
    UNIQUE(manager_id, agent_id),
    INDEX idx_discovered_agents_manager (manager_id),
    INDEX idx_discovered_agents_status (status),
    INDEX idx_discovered_agents_last_seen (last_seen DESC)
);

-- Default manager for agents.ciris.ai
INSERT INTO managers (name, url, description, enabled) 
VALUES ('Production', 'https://agents.ciris.ai', 'Main production CIRISManager', true)
ON CONFLICT (name) DO NOTHING;