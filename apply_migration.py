#!/usr/bin/env python3
"""
Apply database migration for error tracking schema
"""
import asyncio
import asyncpg
import os
from pathlib import Path

async def apply_migration():
    """Apply the error tracking schema migration"""
    database_url = os.getenv('DATABASE_URL', 'postgresql://user:password@host:5432/dbname')
    
    # Read migration file
    migration_path = Path('sql/004_add_error_tracking.sql')
    if not migration_path.exists():
        print(f"ERROR: Migration file {migration_path} not found")
        return False
    
    migration_sql = migration_path.read_text()
    
    try:
        # Connect to database
        conn = await asyncpg.connect(database_url)
        print(f"Connected to database: {database_url}")
        
        # Apply migration
        print("Applying migration: 004_add_error_tracking.sql")
        await conn.execute(migration_sql)
        print("✓ Migration applied successfully")
        
        # Verify tables were created
        result = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'collection_errors'
            )
        """)
        
        if result:
            print("✓ collection_errors table exists")
        else:
            print("✗ collection_errors table not found")
            
        await conn.close()
        return True
        
    except Exception as e:
        print(f"ERROR applying migration: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(apply_migration())