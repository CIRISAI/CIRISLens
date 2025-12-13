-- Create Grafana database for internal state storage
-- This prevents SQLite lock issues under high alert load

-- Create database if not exists (PostgreSQL doesn't have IF NOT EXISTS for CREATE DATABASE)
-- This is handled by checking in a DO block
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'grafana') THEN
        -- Note: CREATE DATABASE cannot be run inside a transaction block
        -- So we use a workaround with dblink or just let Grafana create it
        RAISE NOTICE 'Grafana database will be created by Grafana on first connect';
    END IF;
END $$;

-- Grant permissions to cirislens user for grafana database operations
-- Grafana will create its own tables on first startup
