"""
Extended unit tests for ManagerCollector class.

Tests manager discovery, telemetry collection, and stats.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))

from manager_collector import ManagerCollector


class AsyncContextManagerMock:
    """Helper for async context managers."""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, *args):
        return None


class TestManagerCollectorInit:
    """Tests for ManagerCollector initialization."""

    def test_init_with_database_url(self):
        """Should initialize with database URL."""
        collector = ManagerCollector("postgresql://test")

        assert collector.database_url == "postgresql://test"
        assert collector.pool is None
        assert collector.owns_pool is True
        assert collector.running is False

    def test_init_with_pool(self):
        """Should use provided pool."""
        mock_pool = MagicMock()
        collector = ManagerCollector("postgresql://test", pool=mock_pool)

        assert collector.pool is mock_pool
        assert collector.owns_pool is False


class TestGetEnabledManagers:
    """Tests for get_enabled_managers method."""

    @pytest.mark.asyncio
    async def test_returns_online_managers(self):
        """Should return managers with online status."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"manager_id": "mgr1", "name": "Manager 1", "url": "http://mgr1:8888", "status": "online"},
            {"manager_id": "mgr2", "name": "Manager 2", "url": "http://mgr2:8888", "status": "online"},
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        managers = await collector.get_enabled_managers()

        assert len(managers) == 2
        assert managers[0]["name"] == "Manager 1"


class TestCollectFromManager:
    """Tests for collect_from_manager method."""

    @pytest.mark.asyncio
    async def test_collects_status_and_agents(self):
        """Should collect status and agents from manager."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        manager = {
            "manager_id": "mgr1",
            "name": "Test Manager",
            "url": "http://manager:8888",
            "auth_token": "test-token"
        }

        mock_status_response = MagicMock()
        mock_status_response.status_code = 200
        mock_status_response.json = MagicMock(return_value={"status": "online", "version": "1.0"})

        mock_agents_response = MagicMock()
        mock_agents_response.status_code = 200
        mock_agents_response.json = MagicMock(return_value={
            "agents": [{"agent_id": "agent1", "status": "running"}]
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[mock_status_response, mock_agents_response])
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await collector.collect_from_manager(manager)

        # Should have called execute to store data
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_handles_status_failure(self):
        """Should handle status endpoint failure gracefully."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        manager = {
            "manager_id": "mgr1",
            "name": "Test Manager",
            "url": "http://manager:8888"
        }

        mock_agents_response = MagicMock()
        mock_agents_response.status_code = 200
        mock_agents_response.json = MagicMock(return_value={"agents": []})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[
                Exception("Status failed"),  # Status call fails
                mock_agents_response  # Agents call succeeds
            ])
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should not raise
            await collector.collect_from_manager(manager)

    @pytest.mark.asyncio
    async def test_handles_agents_as_list(self):
        """Should handle agents response as direct list."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        manager = {
            "manager_id": "mgr1",
            "name": "Test Manager",
            "url": "http://manager:8888"
        }

        mock_status_response = MagicMock()
        mock_status_response.status_code = 200
        mock_status_response.json = MagicMock(return_value={})

        mock_agents_response = MagicMock()
        mock_agents_response.status_code = 200
        # Direct list response (not wrapped in dict)
        mock_agents_response.json = MagicMock(return_value=[
            {"agent_id": "agent1", "status": "running"}
        ])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=[mock_status_response, mock_agents_response])
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await collector.collect_from_manager(manager)


class TestStoreManagerTelemetry:
    """Tests for store_manager_telemetry method."""

    @pytest.mark.asyncio
    async def test_updates_manager_last_seen(self):
        """Should update manager last_seen timestamp."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.store_manager_telemetry("mgr1", None, [])

        # Should have called UPDATE managers
        call_args = mock_conn.execute.call_args_list[0][0]
        assert "UPDATE managers SET last_seen" in call_args[0]

    @pytest.mark.asyncio
    async def test_stores_status_data(self):
        """Should store manager telemetry when status data present."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        status_data = {"status": "online", "version": "1.0", "uptime_seconds": 3600}

        await collector.store_manager_telemetry("mgr1", status_data, [])

        # Should have inserted into manager_telemetry
        insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO manager_telemetry" in str(c)]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_stores_discovered_agents(self):
        """Should store discovered agents."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        agents = [
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
                "occurrence_id": "occ1",
                "server_id": "srv1"
            }
        ]

        await collector.store_manager_telemetry("mgr1", None, agents)

        # Should have inserted into discovered_agents
        insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO discovered_agents" in str(c)]
        assert len(insert_calls) == 1


class TestRecordDiscoveryFailure:
    """Tests for record_discovery_failure method."""

    @pytest.mark.asyncio
    async def test_records_failure(self):
        """Should record discovery failure in database."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.record_discovery_failure("mgr1", "Connection timeout")

        call_args = mock_conn.execute.call_args[0]
        assert "INSERT INTO collection_errors" in call_args[0]
        assert "manager_collector:mgr1" in call_args[1]
        assert "DISCOVERY_FAILURE" in call_args[2]

    @pytest.mark.asyncio
    async def test_truncates_long_errors(self):
        """Should truncate error messages longer than 1000 chars."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        long_error = "x" * 2000

        await collector.record_discovery_failure("mgr1", long_error)

        call_args = mock_conn.execute.call_args[0]
        assert len(call_args[3]) == 1000


class TestUpdateManagerError:
    """Tests for update_manager_error method."""

    @pytest.mark.asyncio
    async def test_updates_error_status(self):
        """Should update manager error status."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.update_manager_error(1, "Connection failed")

        call_args = mock_conn.execute.call_args[0]
        assert "UPDATE managers SET last_error" in call_args[0]
        assert call_args[1] == "Connection failed"


class TestAddManager:
    """Tests for add_manager method."""

    @pytest.mark.asyncio
    async def test_adds_new_manager(self):
        """Should add new manager to database."""
        collector = ManagerCollector("postgresql://test")
        collector.running = True

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"manager_id": 1})

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        manager_id = await collector.add_manager(
            name="New Manager",
            url="http://new:8888",
            description="Test manager",
            auth_token="token123",
            collection_interval=60
        )

        assert manager_id == 1
        assert len(collector.tasks) == 1


class TestRemoveManager:
    """Tests for remove_manager method."""

    @pytest.mark.asyncio
    async def test_disables_manager(self):
        """Should disable manager in database."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.remove_manager(1)

        call_args = mock_conn.execute.call_args[0]
        assert "UPDATE managers SET enabled = false" in call_args[0]


class TestGetManagerStats:
    """Tests for get_manager_stats method."""

    @pytest.mark.asyncio
    async def test_returns_stats(self):
        """Should return manager statistics."""
        collector = ManagerCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"count": 3},  # Total managers
            {"count": 5},  # Total agents
            {"count": 1},  # Managers with errors
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        stats = await collector.get_manager_stats()

        assert stats["total_managers"] == 3
        assert stats["total_agents"] == 5
        assert stats["managers_with_errors"] == 1


class TestStartStop:
    """Tests for start and stop methods."""

    @pytest.mark.asyncio
    async def test_start_creates_pool_if_not_provided(self):
        """Should create database pool if not provided."""
        collector = ManagerCollector("postgresql://test")

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        async def mock_create_pool(*args, **kwargs):
            return mock_pool

        with patch("asyncpg.create_pool", side_effect=mock_create_pool) as mock_create:
            await collector.start()

        mock_create.assert_called_once()
        assert collector.running is True

    @pytest.mark.asyncio
    async def test_start_uses_provided_pool(self):
        """Should use provided pool without creating new one."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        collector = ManagerCollector("postgresql://test", pool=mock_pool)

        async def mock_create_pool(*args, **kwargs):
            return MagicMock()

        with patch("asyncpg.create_pool", side_effect=mock_create_pool) as mock_create:
            await collector.start()

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        """Should cancel all tasks on stop."""
        import asyncio

        collector = ManagerCollector("postgresql://test")
        collector.running = True
        collector.owns_pool = False
        collector.pool = None

        # Create a real async task that will be cancelled
        async def dummy_task():
            await asyncio.sleep(100)

        task = asyncio.create_task(dummy_task())
        collector.tasks = [task]

        await collector.stop()

        assert collector.running is False
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_closes_owned_pool(self):
        """Should close pool if collector owns it."""
        collector = ManagerCollector("postgresql://test")
        collector.owns_pool = True
        collector.running = True
        collector.tasks = []

        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()
        collector.pool = mock_pool

        await collector.stop()

        mock_pool.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_close_shared_pool(self):
        """Should not close pool if it was provided."""
        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()

        collector = ManagerCollector("postgresql://test", pool=mock_pool)
        collector.running = True
        collector.tasks = []

        await collector.stop()

        mock_pool.close.assert_not_called()
