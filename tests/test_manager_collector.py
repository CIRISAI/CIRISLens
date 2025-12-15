"""
Unit tests for the CIRISLens Manager Collector using typed mocks
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))

from manager_collector import ManagerCollector


class AsyncContextManagerMock:
    """Helper class to mock async context managers like pool.acquire()"""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


def create_mock_manager(
    manager_id: int = 1,
    name: str = "Test Manager",
    url: str = "https://test.ciris.ai",
    status: str = "online",
    auth_token: str | None = None,
    collection_interval_seconds: int = 30,
) -> dict:
    """Create a mock manager dict matching database row format."""
    return {
        "manager_id": manager_id,
        "name": name,
        "url": url,
        "status": status,
        "auth_token": auth_token,
        "collection_interval_seconds": collection_interval_seconds,
        "last_seen": None,
        "last_error": None,
    }


def create_mock_status() -> dict:
    """Create a mock manager status response."""
    return {
        "status": "running",
        "version": "2.2.0",
        "uptime_seconds": 86400,
    }


def create_mock_agent(agent_id: str = "test-agent", name: str = "TestAgent") -> dict:
    """Create a mock agent response."""
    return {
        "agent_id": agent_id,
        "agent_name": name,
        "status": "running",
        "cognitive_state": "WORK",
        "version": "1.4.5",
        "health": "healthy",
    }


@pytest.fixture
def mock_pool():
    """Create a mock database pool with async context manager support."""
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = AsyncContextManagerMock(conn)
    return pool, conn


@pytest.fixture
def collector(mock_pool):
    """Create a collector instance with mocked database."""
    pool, conn = mock_pool
    collector = ManagerCollector("postgresql://test@localhost/test", pool=pool)
    collector.running = True
    return collector, conn


class TestManagerCollectorInit:
    """Test ManagerCollector initialization."""

    def test_init_with_url(self):
        """Test initialization with database URL."""
        collector = ManagerCollector("postgresql://test@localhost/test")
        assert collector.database_url == "postgresql://test@localhost/test"
        assert collector.pool is None
        assert collector.owns_pool is True
        assert collector.running is False

    def test_init_with_pool(self, mock_pool):
        """Test initialization with existing pool."""
        pool, _ = mock_pool
        collector = ManagerCollector("postgresql://test@localhost/test", pool=pool)
        assert collector.pool == pool
        assert collector.owns_pool is False


class TestGetEnabledManagers:
    """Test getting enabled managers from database."""

    @pytest.mark.asyncio
    async def test_get_enabled_managers_returns_list(self, collector):
        """Test that get_enabled_managers returns a list of dicts."""
        collector_instance, conn = collector

        # Mock database response
        mock_managers = [
            create_mock_manager(1, "Production", "https://prod.ciris.ai"),
            create_mock_manager(2, "Staging", "https://staging.ciris.ai"),
        ]
        conn.fetch.return_value = mock_managers

        result = await collector_instance.get_enabled_managers()

        assert isinstance(result, list)
        assert len(result) == 2
        conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_enabled_managers_empty(self, collector):
        """Test get_enabled_managers with no managers."""
        collector_instance, conn = collector
        conn.fetch.return_value = []

        result = await collector_instance.get_enabled_managers()

        assert result == []


class TestCollectFromManager:
    """Test collecting telemetry from a manager."""

    @pytest.mark.asyncio
    async def test_collect_from_manager_success(self, collector):
        """Test successful collection from a manager."""
        collector_instance, conn = collector

        manager = create_mock_manager()
        status = create_mock_status()
        agents = [create_mock_agent("agent-1"), create_mock_agent("agent-2")]

        # Mock HTTP responses
        mock_status_response = MagicMock()
        mock_status_response.status_code = 200
        mock_status_response.json.return_value = status

        mock_agents_response = MagicMock()
        mock_agents_response.status_code = 200
        mock_agents_response.json.return_value = {"agents": agents}

        with patch("api.manager_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = AsyncContextManagerMock(mock_client)

            mock_client.get.side_effect = [mock_status_response, mock_agents_response]

            await collector_instance.collect_from_manager(manager)

            # Verify HTTP calls were made
            assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_collect_from_manager_with_auth(self, collector):
        """Test collection uses auth token when provided."""
        collector_instance, conn = collector

        manager = create_mock_manager(auth_token="test-token-123")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        with patch("api.manager_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = AsyncContextManagerMock(mock_client)
            mock_client.get.return_value = mock_response

            await collector_instance.collect_from_manager(manager)

            # Verify auth header was included
            calls = mock_client.get.call_args_list
            for call in calls:
                headers = call.kwargs.get("headers", {})
                assert headers.get("Authorization") == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_collect_from_manager_handles_http_error(self, collector):
        """Test graceful handling of HTTP errors."""
        collector_instance, conn = collector

        manager = create_mock_manager()

        with patch("api.manager_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = AsyncContextManagerMock(mock_client)
            mock_client.get.side_effect = Exception("Connection refused")

            # Should not raise, should handle gracefully
            await collector_instance.collect_from_manager(manager)


class TestStoreTelemetry:
    """Test storing telemetry data."""

    @pytest.mark.asyncio
    async def test_store_manager_telemetry(self, collector):
        """Test storing telemetry data to database."""
        collector_instance, conn = collector

        manager_id = 1
        status_data = create_mock_status()
        agents_data = [create_mock_agent()]

        await collector_instance.store_manager_telemetry(
            manager_id, status_data, agents_data
        )

        # Verify database calls were made
        assert conn.execute.called


class TestManagerCollectorLifecycle:
    """Test collector start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, collector):
        """Test that stop() sets running to False."""
        collector_instance, _ = collector
        collector_instance.running = True

        await collector_instance.stop()

        assert collector_instance.running is False
