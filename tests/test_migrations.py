"""Tests for auto-migration system."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from api.migrations import (
    ensure_migrations_table,
    get_applied_migrations,
    validate_schema,
    run_all_migrations,
    REQUIRED_SCHEMA,
)


class TestEnsureMigrationsTable:
    @pytest.mark.asyncio
    async def test_creates_table(self):
        conn = AsyncMock()
        await ensure_migrations_table(conn)
        conn.execute.assert_called_once()
        assert "schema_migrations" in conn.execute.call_args[0][0]


class TestGetAppliedMigrations:
    @pytest.mark.asyncio
    async def test_returns_set_of_filenames(self):
        conn = AsyncMock()
        conn.fetch.return_value = [
            {"filename": "001_init.sql"},
            {"filename": "002_add_users.sql"},
        ]
        result = await get_applied_migrations(conn)
        assert result == {"001_init.sql", "002_add_users.sql"}

    @pytest.mark.asyncio
    async def test_empty_when_no_migrations(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        result = await get_applied_migrations(conn)
        assert result == set()


class TestValidateSchema:
    @pytest.mark.asyncio
    async def test_passes_when_all_columns_exist(self):
        conn = AsyncMock()
        # Table exists
        conn.fetchval.return_value = True
        # All columns exist
        conn.fetch.return_value = [
            {"column_name": col}
            for cols in REQUIRED_SCHEMA.values()
            for col in cols
        ]
        errors = await validate_schema(conn)
        assert errors == []

    @pytest.mark.asyncio
    async def test_fails_when_table_missing(self):
        conn = AsyncMock()
        conn.fetchval.return_value = False  # Table doesn't exist
        errors = await validate_schema(conn)
        assert len(errors) > 0
        assert "does not exist" in errors[0]

    @pytest.mark.asyncio
    async def test_fails_when_column_missing(self):
        conn = AsyncMock()
        conn.fetchval.return_value = True  # Table exists
        conn.fetch.return_value = [{"column_name": "id"}]  # Only id column
        errors = await validate_schema(conn)
        assert len(errors) > 0
        assert "does not exist" in errors[0]


class TestRunAllMigrations:
    @pytest.mark.asyncio
    async def test_applies_new_migrations(self, tmp_path):
        # Create test migration files
        (tmp_path / "001_init.sql").write_text("CREATE TABLE test (id INT);")
        (tmp_path / "002_users.sql").write_text("CREATE TABLE users (id INT);")

        conn = AsyncMock()
        conn.fetch.return_value = []  # No migrations applied yet
        conn.fetchval.return_value = True

        with patch("api.migrations.ensure_migrations_table", new_callable=AsyncMock):
            count = await run_all_migrations(conn, tmp_path)

        assert count == 2
        assert conn.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_skips_already_applied(self, tmp_path):
        (tmp_path / "001_init.sql").write_text("CREATE TABLE test (id INT);")

        conn = AsyncMock()
        conn.fetch.return_value = [{"filename": "001_init.sql"}]  # Already applied

        with patch("api.migrations.ensure_migrations_table", new_callable=AsyncMock):
            count = await run_all_migrations(conn, tmp_path)

        assert count == 0

    @pytest.mark.asyncio
    async def test_ignores_non_numbered_files(self, tmp_path):
        (tmp_path / "manager_tables.sql").write_text("CREATE TABLE managers (id INT);")
        (tmp_path / "001_init.sql").write_text("CREATE TABLE test (id INT);")

        conn = AsyncMock()
        conn.fetch.return_value = []

        with patch("api.migrations.ensure_migrations_table", new_callable=AsyncMock):
            count = await run_all_migrations(conn, tmp_path)

        # Only 001_init.sql should be applied, not manager_tables.sql
        assert count == 1


class TestRequiredSchema:
    def test_has_covenant_traces(self):
        assert "cirislens.covenant_traces" in REQUIRED_SCHEMA

    def test_has_pii_scrubbing_columns(self):
        traces_cols = REQUIRED_SCHEMA["cirislens.covenant_traces"]
        assert "original_content_hash" in traces_cols
        assert "pii_scrubbed" in traces_cols
        assert "scrub_timestamp" in traces_cols
