"""
Unit tests for LogIngestService class.

Tests token management, log ingestion, and PII handling.
"""

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))

from log_ingest import LogIngestService, hash_user_id


class AsyncContextManagerMock:
    """Helper class to mock async context managers like pool.acquire()"""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class TestLogIngestServiceInit:
    """Tests for LogIngestService initialization."""

    def test_init_sets_pool(self):
        """Service should store the pool reference."""
        mock_pool = MagicMock()
        service = LogIngestService(mock_pool)
        assert service.pool is mock_pool

    def test_init_empty_token_cache(self):
        """Service should start with empty token cache."""
        mock_pool = MagicMock()
        service = LogIngestService(mock_pool)
        assert service._token_cache == {}

    def test_init_cache_not_loaded(self):
        """Service should start with cache not loaded."""
        mock_pool = MagicMock()
        service = LogIngestService(mock_pool)
        assert service._cache_loaded is False


class TestLoadTokenCache:
    """Tests for _load_token_cache method."""

    @pytest.mark.asyncio
    async def test_load_token_cache_fetches_tokens(self):
        """Should fetch enabled tokens from database."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"service_name": "billing", "token_hash": "hash1"},
            {"service_name": "proxy", "token_hash": "hash2"},
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        await service._load_token_cache()

        assert service._token_cache == {"billing": "hash1", "proxy": "hash2"}
        assert service._cache_loaded is True

    @pytest.mark.asyncio
    async def test_load_token_cache_skips_if_loaded(self):
        """Should not reload if cache already loaded."""
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()

        service = LogIngestService(mock_pool)
        service._cache_loaded = True
        service._token_cache = {"existing": "token"}

        await service._load_token_cache()

        # Pool should not be accessed
        mock_pool.acquire.assert_not_called()
        assert service._token_cache == {"existing": "token"}

    @pytest.mark.asyncio
    async def test_load_token_cache_handles_empty_result(self):
        """Should handle no tokens in database."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        await service._load_token_cache()

        assert service._token_cache == {}
        assert service._cache_loaded is True


class TestReloadTokens:
    """Tests for reload_tokens method."""

    @pytest.mark.asyncio
    async def test_reload_clears_cache_flag(self):
        """Reload should clear the cache loaded flag."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        service._cache_loaded = True

        await service.reload_tokens()

        # Should have reloaded (fetch was called)
        mock_conn.fetch.assert_called_once()


class TestVerifyToken:
    """Tests for verify_token method."""

    @pytest.mark.asyncio
    async def test_verify_valid_token_returns_service_name(self):
        """Valid token should return service name."""
        test_token = "test-token-123"
        token_hash = hashlib.sha256(test_token.encode()).hexdigest()

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"service_name": "billing", "token_hash": token_hash},
        ])
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.verify_token(test_token)

        assert result == "billing"

    @pytest.mark.asyncio
    async def test_verify_invalid_token_returns_none(self):
        """Invalid token should return None."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"service_name": "billing", "token_hash": "different-hash"},
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.verify_token("wrong-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_updates_last_used(self):
        """Valid token should update last_used_at."""
        test_token = "test-token-123"
        token_hash = hashlib.sha256(test_token.encode()).hexdigest()

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"service_name": "billing", "token_hash": token_hash},
        ])
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        await service.verify_token(test_token)

        # Should have called execute to update last_used_at
        mock_conn.execute.assert_called()
        call_args = mock_conn.execute.call_args[0]
        assert "UPDATE" in call_args[0]
        assert "last_used_at" in call_args[0]


class TestCreateToken:
    """Tests for create_token method."""

    @pytest.mark.asyncio
    async def test_create_token_returns_raw_token(self):
        """Should return the raw token string."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.create_token("test-service", "admin@ciris.ai", "Test token")

        assert result.startswith("svc_")
        assert len(result) > 20  # Token should be substantial

    @pytest.mark.asyncio
    async def test_create_token_inserts_to_db(self):
        """Should insert token hash to database."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        await service.create_token("test-service", "admin@ciris.ai", "Test token")

        # Find the INSERT call
        insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT" in str(c)]
        assert len(insert_calls) >= 1

    @pytest.mark.asyncio
    async def test_create_token_reloads_cache(self):
        """Should reload cache after creating token."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        service._cache_loaded = True

        await service.create_token("test-service", "admin@ciris.ai")

        # Cache should have been reloaded (fetch called)
        assert mock_conn.fetch.called


class TestRevokeToken:
    """Tests for revoke_token method."""

    @pytest.mark.asyncio
    async def test_revoke_token_disables_in_db(self):
        """Should set enabled=FALSE in database."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.revoke_token("test-service")

        assert result is True
        # Should have called UPDATE
        update_calls = [c for c in mock_conn.execute.call_args_list if "enabled = FALSE" in str(c)]
        assert len(update_calls) >= 1

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_token_returns_false(self):
        """Should return False for nonexistent token."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.revoke_token("nonexistent")

        assert result is False


class TestGetTokens:
    """Tests for get_tokens method."""

    @pytest.mark.asyncio
    async def test_get_tokens_returns_list(self):
        """Should return list of token info."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "service_name": "billing",
                "description": "Billing service",
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                "created_by": "admin",
                "last_used_at": datetime(2024, 1, 15, tzinfo=UTC),
                "enabled": True,
            },
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.get_tokens()

        assert len(result) == 1
        assert result[0]["service_name"] == "billing"
        assert result[0]["enabled"] is True
        assert "created_at" in result[0]

    @pytest.mark.asyncio
    async def test_get_tokens_handles_null_dates(self):
        """Should handle null dates gracefully."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "service_name": "new-service",
                "description": None,
                "created_at": None,
                "created_by": "admin",
                "last_used_at": None,
                "enabled": True,
            },
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        result = await service.get_tokens()

        assert result[0]["created_at"] is None
        assert result[0]["last_used_at"] is None


class TestIngestLogs:
    """Tests for ingest_logs method."""

    @pytest.mark.asyncio
    async def test_ingest_simple_log(self):
        """Should ingest a simple log entry."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{"message": "Test log message", "level": "INFO"}]

        result = await service.ingest_logs("billing", logs)

        assert result["accepted"] == 1
        assert result["rejected"] == 0

    @pytest.mark.asyncio
    async def test_ingest_log_with_timestamp_string(self):
        """Should handle ISO timestamp strings."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{
            "message": "Test log",
            "timestamp": "2024-01-15T10:30:00Z",
            "level": "WARNING"
        }]

        result = await service.ingest_logs("proxy", logs)

        assert result["accepted"] == 1

    @pytest.mark.asyncio
    async def test_ingest_log_sanitizes_pii(self):
        """Should sanitize PII in log messages."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{"message": "User email is test@example.com"}]

        await service.ingest_logs("billing", logs)

        # Check that the sanitized message was passed
        call_args = mock_conn.execute.call_args[0]
        # Message is arg index 6 (after service_name, server_id, timestamp, level, event, logger)
        assert "[EMAIL]" in call_args[7] or "test@example.com" not in str(call_args)

    @pytest.mark.asyncio
    async def test_ingest_log_hashes_user_id(self):
        """Should hash user_id in attributes."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{
            "message": "User action",
            "attributes": {"user_id": "user123", "other": "data"}
        }]

        await service.ingest_logs("billing", logs)

        # Should have called execute with hashed user_id
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        # user_hash is arg index 9
        assert call_args[10] == hash_user_id("user123")

    @pytest.mark.asyncio
    async def test_ingest_log_preserves_user_hash(self):
        """Should preserve existing user_hash."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{
            "message": "User action",
            "user_hash": "existing_hash_value"
        }]

        await service.ingest_logs("billing", logs)

        call_args = mock_conn.execute.call_args[0]
        assert call_args[10] == "existing_hash_value"

    @pytest.mark.asyncio
    async def test_ingest_invalid_level_defaults_to_info(self):
        """Should default invalid log level to INFO."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{"message": "Test", "level": "INVALID_LEVEL"}]

        await service.ingest_logs("billing", logs)

        call_args = mock_conn.execute.call_args[0]
        # level is arg index 4
        assert call_args[4] == "INFO"

    @pytest.mark.asyncio
    async def test_ingest_multiple_logs(self):
        """Should handle multiple logs in batch."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [
            {"message": "Log 1", "level": "INFO"},
            {"message": "Log 2", "level": "WARNING"},
            {"message": "Log 3", "level": "ERROR"},
        ]

        result = await service.ingest_logs("billing", logs)

        assert result["accepted"] == 3
        assert result["rejected"] == 0
        assert mock_conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_ingest_handles_db_error(self):
        """Should handle database errors gracefully."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{"message": "Test log"}]

        result = await service.ingest_logs("billing", logs)

        assert result["accepted"] == 0
        assert result["rejected"] == 1
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_ingest_limits_error_messages(self):
        """Should limit error messages to 10."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{"message": f"Log {i}"} for i in range(20)]

        result = await service.ingest_logs("billing", logs)

        assert result["rejected"] == 20
        assert len(result["errors"]) == 10  # Limited to 10

    @pytest.mark.asyncio
    async def test_ingest_all_log_levels(self):
        """Should accept all valid log levels."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [
            {"message": "Debug", "level": "DEBUG"},
            {"message": "Info", "level": "INFO"},
            {"message": "Warning", "level": "WARNING"},
            {"message": "Error", "level": "ERROR"},
            {"message": "Critical", "level": "CRITICAL"},
        ]

        result = await service.ingest_logs("billing", logs)

        assert result["accepted"] == 5

    @pytest.mark.asyncio
    async def test_ingest_with_all_fields(self):
        """Should handle log with all optional fields."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        service = LogIngestService(mock_pool)
        logs = [{
            "message": "Full log entry",
            "level": "INFO",
            "timestamp": "2024-01-15T10:30:00Z",
            "server_id": "server-1",
            "event": "user.login",
            "logger": "auth.service",
            "request_id": "req-123",
            "trace_id": "trace-456",
            "attributes": {"key": "value"}
        }]

        result = await service.ingest_logs("billing", logs)

        assert result["accepted"] == 1
        mock_conn.execute.assert_called_once()
