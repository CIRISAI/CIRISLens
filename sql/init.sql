-- CIRISLens Database Schema
-- Stores telemetry and visibility configurations

CREATE SCHEMA IF NOT EXISTS cirislens;

-- Managers table
CREATE TABLE IF NOT EXISTS cirislens.managers (
    manager_id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    url VARCHAR(512) NOT NULL,
    status VARCHAR(50) DEFAULT 'offline',
    version VARCHAR(50),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    agent_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Agents table (cached from manager discovery)
CREATE TABLE IF NOT EXISTS cirislens.agents (
    agent_id VARCHAR(255) PRIMARY KEY,
    manager_id VARCHAR(255) REFERENCES cirislens.managers(manager_id),
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50),
    cognitive_state VARCHAR(50),
    version VARCHAR(50),
    codename VARCHAR(255),
    api_port INTEGER,
    health VARCHAR(50),
    container_id VARCHAR(255),
    deployment_type VARCHAR(50),
    ip_address INET,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Telemetry configurations
CREATE TABLE IF NOT EXISTS cirislens.telemetry_configs (
    agent_id VARCHAR(255) PRIMARY KEY,
    enabled BOOLEAN DEFAULT FALSE,
    collection_interval INTEGER DEFAULT 60,
    metrics_enabled BOOLEAN DEFAULT TRUE,
    traces_enabled BOOLEAN DEFAULT TRUE,
    logs_enabled BOOLEAN DEFAULT TRUE,
    scrape_endpoint VARCHAR(512),
    custom_labels JSONB,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Dashboard visibility configurations
CREATE TABLE IF NOT EXISTS cirislens.visibility_configs (
    agent_id VARCHAR(255) PRIMARY KEY,
    public_visible BOOLEAN DEFAULT FALSE,
    show_metrics BOOLEAN DEFAULT TRUE,
    show_traces BOOLEAN DEFAULT FALSE,
    show_logs BOOLEAN DEFAULT FALSE,
    show_cognitive_state BOOLEAN DEFAULT TRUE,
    show_health_status BOOLEAN DEFAULT TRUE,
    redact_pii BOOLEAN DEFAULT TRUE,
    hash_agent_id BOOLEAN DEFAULT TRUE,
    allowed_metrics TEXT[],
    blocked_metrics TEXT[],
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- OAuth sessions
CREATE TABLE IF NOT EXISTS cirislens.sessions (
    session_id VARCHAR(255) PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    user_name VARCHAR(255),
    user_picture TEXT,
    user_domain VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Audit log
CREATE TABLE IF NOT EXISTS cirislens.audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    user_email VARCHAR(255),
    action VARCHAR(100),
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    old_value JSONB,
    new_value JSONB,
    ip_address INET,
    user_agent TEXT
);

-- Indexes
CREATE INDEX idx_agents_manager_id ON cirislens.agents(manager_id);
CREATE INDEX idx_agents_status ON cirislens.agents(status);
CREATE INDEX idx_telemetry_enabled ON cirislens.telemetry_configs(enabled);
CREATE INDEX idx_visibility_public ON cirislens.visibility_configs(public_visible);
CREATE INDEX idx_sessions_email ON cirislens.sessions(user_email);
CREATE INDEX idx_sessions_expires ON cirislens.sessions(expires_at);
CREATE INDEX idx_audit_timestamp ON cirislens.audit_log(timestamp);
CREATE INDEX idx_audit_user ON cirislens.audit_log(user_email);

-- Views
CREATE OR REPLACE VIEW cirislens.agent_admin_view AS
SELECT 
    a.agent_id,
    a.name,
    a.status,
    a.cognitive_state,
    a.version,
    a.codename,
    a.health,
    a.api_port,
    a.manager_id,
    m.name as manager_name,
    m.url as manager_url,
    t.enabled as telemetry_enabled,
    t.collection_interval,
    t.metrics_enabled,
    t.traces_enabled,
    t.logs_enabled,
    v.public_visible,
    v.show_metrics,
    v.show_traces,
    v.show_logs,
    v.show_cognitive_state,
    v.show_health_status,
    v.redact_pii
FROM cirislens.agents a
LEFT JOIN cirislens.managers m ON a.manager_id = m.manager_id
LEFT JOIN cirislens.telemetry_configs t ON a.agent_id = t.agent_id
LEFT JOIN cirislens.visibility_configs v ON a.agent_id = v.agent_id;

-- Functions
CREATE OR REPLACE FUNCTION cirislens.update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers
CREATE TRIGGER update_managers_timestamp
    BEFORE UPDATE ON cirislens.managers
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

CREATE TRIGGER update_agents_timestamp
    BEFORE UPDATE ON cirislens.agents
    FOR EACH ROW
    EXECUTE FUNCTION cirislens.update_timestamp();

-- Initial data
INSERT INTO cirislens.managers (manager_id, name, url, status)
VALUES ('primary', 'Primary Manager', 'http://host.docker.internal:8888/manager/v1', 'online')
ON CONFLICT (manager_id) DO NOTHING;

-- Permissions
GRANT ALL PRIVILEGES ON SCHEMA cirislens TO cirislens;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cirislens TO cirislens;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cirislens TO cirislens;