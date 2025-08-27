-- Migration to add manager_id and status columns to existing managers table
-- This handles the case where the database already exists

-- Add manager_id column if it doesn't exist
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='managers' AND column_name='manager_id') THEN
        ALTER TABLE managers ADD COLUMN manager_id VARCHAR(255) UNIQUE NOT NULL DEFAULT gen_random_uuid()::text;
    END IF;
END $$;

-- Add status column if it doesn't exist
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='managers' AND column_name='status') THEN
        ALTER TABLE managers ADD COLUMN status VARCHAR(50) DEFAULT 'online';
    END IF;
END $$;

-- Update existing managers to have status = 'online' if enabled
UPDATE managers SET status = 'online' WHERE enabled = true AND status IS NULL;
UPDATE managers SET status = 'offline' WHERE enabled = false AND status IS NULL;