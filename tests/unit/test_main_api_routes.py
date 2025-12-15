"""
Additional unit tests for main.py API routes to increase coverage.

Tests service log endpoints, admin manager endpoints with database operations.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))


class AsyncContextManagerMock:
    """Helper for async context managers."""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, *args):
        return None


class TestServiceLogTokenEndpoints:
    """Tests for service log token management endpoints."""

    @pytest.mark.asyncio
    async def test_create_service_token(self):
        """Should create a new service token."""
        from main import create_service_token

        user = {"email": "admin@ciris.ai"}

        mock_service = MagicMock()
        mock_service.create_token = AsyncMock(return_value="svc_test123")

        with patch("main.log_ingest_service", mock_service):
            result = await create_service_token(
                MagicMock(service_name="billing", description="Test token"),
                user
            )

        assert result["token"] == "svc_test123"
        assert result["service_name"] == "billing"

    @pytest.mark.asyncio
    async def test_create_service_token_requires_service(self):
        """Should return 503 if log_ingest_service not available."""
        from fastapi import HTTPException

        from main import create_service_token

        user = {"email": "admin@ciris.ai"}

        with patch("main.log_ingest_service", None):
            with pytest.raises(HTTPException) as exc_info:
                await create_service_token(
                    MagicMock(service_name="billing"),
                    user
                )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_service_tokens(self):
        """Should list all service tokens."""
        from main import get_service_tokens

        user = {"email": "admin@ciris.ai"}

        mock_service = MagicMock()
        mock_service.get_tokens = AsyncMock(return_value=[
            {"service_name": "billing", "enabled": True}
        ])

        with patch("main.log_ingest_service", mock_service):
            result = await get_service_tokens(user)

        assert "tokens" in result
        assert len(result["tokens"]) == 1

    @pytest.mark.asyncio
    async def test_revoke_service_token(self):
        """Should revoke a service token."""
        from main import revoke_service_token

        user = {"email": "admin@ciris.ai"}

        mock_service = MagicMock()
        mock_service.revoke_token = AsyncMock(return_value=True)

        with patch("main.log_ingest_service", mock_service):
            result = await revoke_service_token("billing", user)

        assert result["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_revoke_service_token_not_found(self):
        """Should return 404 if token not found."""
        from fastapi import HTTPException

        from main import revoke_service_token

        user = {"email": "admin@ciris.ai"}

        mock_service = MagicMock()
        mock_service.revoke_token = AsyncMock(return_value=False)

        with patch("main.log_ingest_service", mock_service):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_service_token("unknown", user)

        assert exc_info.value.status_code == 404


class TestServiceLogsEndpoint:
    """Tests for service logs retrieval endpoint."""

    @pytest.mark.asyncio
    async def test_get_service_logs(self):
        """Should return service logs."""
        from main import get_service_logs

        user = {"email": "admin@ciris.ai"}

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "id": 1,
                "service_name": "billing",
                "server_id": "server-1",
                "timestamp": datetime.now(UTC),
                "level": "INFO",
                "event": "request_completed",
                "logger": "main",
                "message": "OK",
                "request_id": "req-123",
                "trace_id": "trace-456",
                "user_hash": None,
                "attributes": {}
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await get_service_logs(
                service_name=None,
                level=None,
                limit=100,
                user=user
            )

        assert "logs" in result
        assert len(result["logs"]) == 1

    @pytest.mark.asyncio
    async def test_get_service_logs_with_filters(self):
        """Should filter logs by service and level."""
        from main import get_service_logs

        user = {"email": "admin@ciris.ai"}

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await get_service_logs(
                service_name="billing",
                level="ERROR",
                limit=50,
                user=user
            )

        assert "logs" in result
        # Check that fetch was called (filters applied in query)
        mock_conn.fetch.assert_called_once()


class TestUpdateManagerEndpoint:
    """Tests for update manager endpoint."""

    @pytest.mark.asyncio
    async def test_update_manager_single_field(self):
        """Should update a single field."""
        from main import ManagerUpdate, update_manager

        user = {"email": "admin@ciris.ai"}
        updates = ManagerUpdate(name="New Name")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await update_manager(1, updates, user)

        assert result["status"] == "updated"

    @pytest.mark.asyncio
    async def test_update_manager_multiple_fields(self):
        """Should update multiple fields."""
        from main import ManagerUpdate, update_manager

        user = {"email": "admin@ciris.ai"}
        updates = ManagerUpdate(
            name="New Name",
            url="http://new:8888",
            enabled=True,
            collection_interval_seconds=60
        )

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await update_manager(1, updates, user)

        assert result["status"] == "updated"

    @pytest.mark.asyncio
    async def test_update_manager_no_changes(self):
        """Should return no_changes if no fields provided."""
        from main import ManagerUpdate, update_manager

        user = {"email": "admin@ciris.ai"}
        updates = ManagerUpdate()  # No fields set

        mock_pool = MagicMock()

        with patch("main.db_pool", mock_pool):
            result = await update_manager(1, updates, user)

        assert result["status"] == "no_changes"


class TestAddManagerEndpoint:
    """Tests for add manager endpoint."""

    @pytest.mark.asyncio
    async def test_add_manager_success(self):
        """Should add a new manager."""
        from main import ManagerConfig, add_manager

        user = {"email": "admin@ciris.ai"}
        config = ManagerConfig(
            name="New Manager",
            url="http://manager:8888",
            description="Test manager"
        )

        mock_collector = MagicMock()
        mock_collector.add_manager = AsyncMock(return_value=1)

        mock_pool = MagicMock()

        with patch("main.db_pool", mock_pool):
            with patch("main.manager_collector", mock_collector):
                result = await add_manager(config, user)

        assert result["status"] == "created"
        assert result["manager_id"] == 1


class TestGetManagerAgentsEndpoint:
    """Tests for get manager agents endpoint."""

    @pytest.mark.asyncio
    async def test_get_manager_agents_returns_list(self):
        """Should return list of agents for manager."""
        from main import get_manager_agents

        user = {"email": "admin@ciris.ai"}

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "agent_id": "agent1",
                "agent_name": "Test Agent",
                "status": "running",
                "cognitive_state": "WORK",
                "version": "1.0",
                "codename": "Alpha",
                "api_port": 8080,
                "health": "healthy",
                "template": "default",
                "deployment": "docker",
                "last_seen": datetime.now(UTC)
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await get_manager_agents(1, user)

        assert "agents" in result
        assert len(result["agents"]) == 1
        assert result["agents"][0]["agent_id"] == "agent1"


class TestGetAllDiscoveredAgentsEndpoint:
    """Tests for get all discovered agents endpoint."""

    @pytest.mark.asyncio
    async def test_get_all_discovered_agents_returns_list(self):
        """Should return all agents across managers."""
        from main import get_all_discovered_agents

        user = {"email": "admin@ciris.ai"}

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "agent_id": "agent1",
                "agent_name": "Test Agent",
                "status": "running",
                "cognitive_state": "WORK",
                "version": "1.0",
                "codename": "Alpha",
                "api_port": 8080,
                "health": "healthy",
                "template": "default",
                "deployment": "docker",
                "manager_name": "Main Manager",
                "manager_url": "http://manager:8888",
                "last_seen": datetime.now(UTC)
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await get_all_discovered_agents(user)

        assert "agents" in result
        assert result["agents"][0]["manager_name"] == "Main Manager"


class TestGetStatsEndpoint:
    """Tests for get stats endpoint."""

    @pytest.mark.asyncio
    async def test_get_stats_returns_data(self):
        """Should return stats from manager collector."""
        from main import get_stats

        user = {"email": "admin@ciris.ai"}

        mock_collector = MagicMock()
        mock_collector.get_manager_stats = AsyncMock(return_value={
            "total_managers": 2,
            "total_agents": 5
        })

        with patch("main.manager_collector", mock_collector):
            result = await get_stats(user)

        assert result["stats"]["total_managers"] == 2


class TestIngestLogsEndpoint:
    """Tests for log ingestion endpoint with body parsing."""

    @pytest.mark.asyncio
    async def test_ingest_json_logs(self):
        """Should ingest JSON log array."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(side_effect=lambda k, d="": {
            "Authorization": "Bearer valid-token",
            "Content-Type": "application/json"
        }.get(k, d))
        mock_request.body = AsyncMock(return_value=b'[{"message": "test", "level": "INFO"}]')

        mock_service = MagicMock()
        mock_service.verify_token = AsyncMock(return_value="billing")
        mock_service.ingest_logs = AsyncMock(return_value={
            "accepted": 1, "rejected": 0, "errors": []
        })

        with patch("main.log_ingest_service", mock_service):
            result = await ingest_logs(mock_request)

        assert result["accepted"] == 1

    @pytest.mark.asyncio
    async def test_ingest_ndjson_logs(self):
        """Should ingest newline-delimited JSON logs."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(side_effect=lambda k, d="": {
            "Authorization": "Bearer valid-token",
            "Content-Type": "application/x-ndjson"
        }.get(k, d))
        mock_request.body = AsyncMock(
            return_value=b'{"message": "log1"}\n{"message": "log2"}'
        )

        mock_service = MagicMock()
        mock_service.verify_token = AsyncMock(return_value="billing")
        mock_service.ingest_logs = AsyncMock(return_value={
            "accepted": 2, "rejected": 0, "errors": []
        })

        with patch("main.log_ingest_service", mock_service):
            result = await ingest_logs(mock_request)

        assert result["accepted"] == 2

    @pytest.mark.asyncio
    async def test_ingest_single_json_log(self):
        """Should ingest single JSON log object."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(side_effect=lambda k, d="": {
            "Authorization": "Bearer valid-token",
            "Content-Type": "application/json"
        }.get(k, d))
        mock_request.body = AsyncMock(return_value=b'{"message": "single log"}')

        mock_service = MagicMock()
        mock_service.verify_token = AsyncMock(return_value="billing")
        mock_service.ingest_logs = AsyncMock(return_value={
            "accepted": 1, "rejected": 0, "errors": []
        })

        with patch("main.log_ingest_service", mock_service):
            result = await ingest_logs(mock_request)

        assert result["accepted"] == 1
