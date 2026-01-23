"""
Auto-migration system for CIRISLens.

Automatically applies all numbered SQL migrations on startup.
Tracks applied migrations in a `cirislens.schema_migrations` table.
"""

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Required columns that MUST exist for the covenant API to function
# If any are missing, startup should FAIL LOUDLY
# Only include tables that are strictly required for covenant traces
REQUIRED_SCHEMA = {
    "cirislens.covenant_traces": [
        "trace_id",
        "timestamp",
        "original_content_hash",
        "pii_scrubbed",
        "scrub_timestamp",
        "scrub_signature",
        "scrub_key_id",
    ],
}


async def ensure_migrations_table(conn) -> None:
    """Create the migrations tracking table if it doesn't exist."""
    # Use DO block to handle race conditions gracefully
    await conn.execute("""
        DO $$
        BEGIN
            CREATE TABLE IF NOT EXISTS cirislens.schema_migrations (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) NOT NULL UNIQUE,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                checksum VARCHAR(64)
            );
        EXCEPTION
            WHEN duplicate_table THEN NULL;
            WHEN unique_violation THEN NULL;
        END $$;
    """)


async def get_applied_migrations(conn) -> set[str]:
    """Get set of already-applied migration filenames."""
    rows = await conn.fetch(
        "SELECT filename FROM cirislens.schema_migrations"
    )
    return {row["filename"] for row in rows}


async def apply_migration(conn, filepath: Path) -> bool:
    """Apply a single migration file."""
    filename = filepath.name
    sql_content = filepath.read_text()

    try:
        await conn.execute(sql_content)

        # Record as applied
        checksum = hashlib.sha256(sql_content.encode()).hexdigest()[:16]
        await conn.execute(
            """
            INSERT INTO cirislens.schema_migrations (filename, checksum)
            VALUES ($1, $2)
            ON CONFLICT (filename) DO NOTHING
            """,
            filename,
            checksum,
        )
        logger.info("Applied migration: %s", filename)
        return True
    except Exception as e:
        # Check if it's a "already exists" type error (idempotent migrations)
        error_str = str(e).lower()
        if "already exists" in error_str or "duplicate" in error_str:
            logger.info("Migration %s: already applied (idempotent)", filename)
            # Record as applied anyway
            await conn.execute(
                """
                INSERT INTO cirislens.schema_migrations (filename, checksum)
                VALUES ($1, 'idempotent')
                ON CONFLICT (filename) DO NOTHING
                """,
                filename,
            )
            return True
        else:
            logger.error("Failed to apply migration %s: %s", filename, e)
            raise


async def run_all_migrations(conn, sql_dir: Path = Path("/app/sql")) -> int:
    """
    Run all numbered migrations that haven't been applied yet.

    Returns number of migrations applied.
    """
    await ensure_migrations_table(conn)
    applied = await get_applied_migrations(conn)

    # Find all numbered migration files (e.g., 001_xxx.sql, 012_xxx.sql)
    migration_pattern = re.compile(r"^(\d{3})_.+\.sql$")
    migration_files = []

    if sql_dir.exists():
        for f in sql_dir.iterdir():
            match = migration_pattern.match(f.name)
            if match:
                migration_files.append((int(match.group(1)), f))

    # Sort by number
    migration_files.sort(key=lambda x: x[0])

    count = 0
    for num, filepath in migration_files:
        if filepath.name not in applied:
            logger.info("Applying migration %03d: %s", num, filepath.name)
            await apply_migration(conn, filepath)
            count += 1

    if count == 0:
        logger.info("All migrations already applied")
    else:
        logger.info("Applied %d migrations", count)

    return count


async def validate_schema(conn) -> list[str]:
    """
    Validate that all required schema elements exist.

    Returns list of errors. Empty list means schema is valid.
    """
    errors = []

    for table, required_columns in REQUIRED_SCHEMA.items():
        schema, table_name = table.split(".")

        # Check table exists
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = $1 AND table_name = $2
            )
            """,
            schema,
            table_name,
        )

        if not exists:
            errors.append(f"CRITICAL: Table {table} does not exist!")
            continue

        # Check required columns
        rows = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            """,
            schema,
            table_name,
        )
        existing_columns = {row["column_name"] for row in rows}

        for col in required_columns:
            if col not in existing_columns:
                errors.append(
                    f"CRITICAL: Column {table}.{col} does not exist! "
                    f"Run migration to add it."
                )

    return errors


async def startup_migrations(conn, sql_dir: Path = Path("/app/sql")) -> None:
    """
    Run migrations and validate schema on startup.

    Raises RuntimeError if schema validation fails after migrations.
    """
    # Run all pending migrations
    await run_all_migrations(conn, sql_dir)

    # Validate schema
    errors = await validate_schema(conn)

    if errors:
        for error in errors:
            logger.error(error)
        raise RuntimeError(
            f"Schema validation failed with {len(errors)} errors. "
            "Database schema is incompatible with code. "
            "Check migrations and redeploy."
        )

    logger.info("Schema validation passed - all required columns present")
