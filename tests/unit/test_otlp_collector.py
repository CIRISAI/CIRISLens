"""
Unit tests for OTLPCollector class.

Tests agent discovery, OTLP data collection, and signal processing.
"""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestOTLPCollectorInit:
    """Test OTLPCollector initialization."""

    def test_init_sets_database_url(self):
        """Test that constructor sets database URL."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        assert collector.database_url == "postgresql://test@localhost/testdb"
        assert collector.pool is None
        assert collector.running is False
        assert collector.tasks == []
        assert collector.agent_configs == {}

    def test_init_reads_refresh_interval_from_env(self):
        """Test that refresh interval is read from environment."""
        from api.otlp_collector import OTLPCollector

        os.environ["AGENT_DISCOVERY_INTERVAL"] = "120"
        try:
            collector = OTLPCollector("postgresql://test@localhost/testdb")
            assert collector.refresh_interval == 120
        finally:
            del os.environ["AGENT_DISCOVERY_INTERVAL"]

    def test_init_defaults_refresh_interval(self):
        """Test default refresh interval when not in environment."""
        from api.otlp_collector import OTLPCollector

        # Ensure env var is not set
        original = os.environ.pop("AGENT_DISCOVERY_INTERVAL", None)
        try:
            collector = OTLPCollector("postgresql://test@localhost/testdb")
            assert collector.refresh_interval == 60
        finally:
            if original:
                os.environ["AGENT_DISCOVERY_INTERVAL"] = original


class TestLoadAgentConfigsFromEnv:
    """Test environment-based agent configuration loading."""

    def test_load_from_env_finds_token_url_pairs(self):
        """Test that matching TOKEN/URL pairs are loaded."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        # Set up test env vars
        os.environ["AGENT_MYBOT_TOKEN"] = "test-token-123"
        os.environ["AGENT_MYBOT_URL"] = "http://localhost:8080"

        try:
            configs = collector._load_agent_configs_from_env()

            assert "env_mybot" in configs
            assert configs["env_mybot"]["url"] == "http://localhost:8080"
            assert configs["env_mybot"]["token"] == "test-token-123"
            assert configs["env_mybot"]["name"] == "mybot"
        finally:
            del os.environ["AGENT_MYBOT_TOKEN"]
            del os.environ["AGENT_MYBOT_URL"]

    def test_load_from_env_skips_token_without_url(self):
        """Test that tokens without matching URLs are skipped."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        os.environ["AGENT_ORPHAN_TOKEN"] = "orphan-token"
        # No matching URL

        try:
            configs = collector._load_agent_configs_from_env()
            assert "env_orphan" not in configs
        finally:
            del os.environ["AGENT_ORPHAN_TOKEN"]

    def test_load_from_env_strips_trailing_slash_from_url(self):
        """Test that trailing slashes are removed from URLs."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        os.environ["AGENT_SLASHTEST_TOKEN"] = "token"
        os.environ["AGENT_SLASHTEST_URL"] = "http://localhost:8080/"

        try:
            configs = collector._load_agent_configs_from_env()
            assert configs["env_slashtest"]["url"] == "http://localhost:8080"
        finally:
            del os.environ["AGENT_SLASHTEST_TOKEN"]
            del os.environ["AGENT_SLASHTEST_URL"]

    def test_load_from_env_multiple_agents(self):
        """Test loading multiple agents from environment."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        os.environ["AGENT_AGENT1_TOKEN"] = "token1"
        os.environ["AGENT_AGENT1_URL"] = "http://agent1:8080"
        os.environ["AGENT_AGENT2_TOKEN"] = "token2"
        os.environ["AGENT_AGENT2_URL"] = "http://agent2:8080"

        try:
            configs = collector._load_agent_configs_from_env()
            assert len(configs) >= 2
            assert "env_agent1" in configs
            assert "env_agent2" in configs
        finally:
            del os.environ["AGENT_AGENT1_TOKEN"]
            del os.environ["AGENT_AGENT1_URL"]
            del os.environ["AGENT_AGENT2_TOKEN"]
            del os.environ["AGENT_AGENT2_URL"]


class TestFetchOTLPSignal:
    """Test _fetch_otlp_signal method."""

    @pytest.mark.asyncio
    async def test_fetch_returns_json_on_200(self):
        """Test successful fetch returns JSON data."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"metrics": [{"name": "test_metric"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        result = await collector._fetch_otlp_signal(
            mock_client, "http://agent/v1/telemetry/otlp/metrics", {"Authorization": "Bearer token"}
        )

        assert result == {"metrics": [{"name": "test_metric"}]}
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_returns_none_on_non_200(self):
        """Test non-200 response returns None."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        result = await collector._fetch_otlp_signal(mock_client, "http://agent/v1/telemetry", {})

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_returns_none_on_exception(self):
        """Test exception handling returns None."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")

        result = await collector._fetch_otlp_signal(mock_client, "http://agent/v1/telemetry", {})

        assert result is None


class TestCollectorLifecycle:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        """Test that stop() sets running to False."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.running = True
        collector.pool = AsyncMock()
        collector.pool.close = AsyncMock()
        collector.tasks = []

        await collector.stop()

        assert collector.running is False
        collector.pool.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        """Test that stop() cancels all running tasks."""
        import asyncio
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.running = True

        # Create real async tasks that we can cancel
        async def dummy_task():
            await asyncio.sleep(100)

        task1 = asyncio.create_task(dummy_task())
        task2 = asyncio.create_task(dummy_task())

        collector.tasks = [task1, task2]
        collector.pool = AsyncMock()
        collector.pool.close = AsyncMock()

        await collector.stop()

        # Tasks should be cancelled
        assert task1.cancelled()
        assert task2.cancelled()


class TestCollectOTLPData:
    """Test collect_otlp_data method."""

    @pytest.mark.asyncio
    async def test_collect_otlp_data_fetches_all_signals(self):
        """Test that all three signal types are fetched."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.store_otlp_data = AsyncMock()

        config = {
            "name": "test-agent",
            "url": "http://localhost:8080",
            "token": "test-token"
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}

        with patch("api.otlp_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            await collector.collect_otlp_data(config)

            # Should have called get 3 times (metrics, traces, logs)
            assert mock_client.get.call_count == 3

            # Verify URLs called
            calls = mock_client.get.call_args_list
            urls = [call.args[0] for call in calls]
            assert "http://localhost:8080/v1/telemetry/otlp/metrics" in urls
            assert "http://localhost:8080/v1/telemetry/otlp/traces" in urls
            assert "http://localhost:8080/v1/telemetry/otlp/logs" in urls

            # Verify store_otlp_data was called
            collector.store_otlp_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_otlp_data_uses_auth_header(self):
        """Test that Authorization header is included."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.store_otlp_data = AsyncMock()

        config = {
            "name": "test-agent",
            "url": "http://localhost:8080",
            "token": "secret-token-xyz"
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        with patch("api.otlp_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            await collector.collect_otlp_data(config)

            # Check authorization header
            call_args = mock_client.get.call_args_list[0]
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer secret-token-xyz"


class TestStoreCollectionError:
    """Test store_collection_error method."""

    @pytest.mark.asyncio
    async def test_store_collection_error_writes_to_db(self):
        """Test that collection errors are stored in database."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        # Mock pool and connection
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        collector.pool = mock_pool

        await collector.store_collection_error("test-agent", "Connection timeout")

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "test-agent" in call_args.args
        assert "Connection timeout" in call_args.args


class TestRefreshAgentConfigs:
    """Test _refresh_agent_configs method."""

    @pytest.mark.asyncio
    async def test_refresh_adds_new_agents(self):
        """Test that new agents are added when refreshed."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.running = True
        collector.agent_configs = {}
        collector.agent_tasks = {}

        new_configs = {
            "agent-1": {
                "url": "http://agent1:8080",
                "token": "token1",
                "name": "Agent 1",
                "agent_id": "agent-1"
            }
        }

        collector._load_agent_configs_from_db = AsyncMock(return_value=new_configs)

        with patch("asyncio.create_task") as mock_create_task:
            mock_task = MagicMock()
            mock_create_task.return_value = mock_task

            await collector._refresh_agent_configs()

            # Check agent was added
            assert "agent-1" in collector.agent_configs
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_removes_old_agents(self):
        """Test that removed agents are cleaned up."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")
        collector.running = True

        # Start with an existing agent
        mock_task = MagicMock()
        collector.agent_configs = {
            "old-agent": {
                "url": "http://old:8080",
                "token": "token",
                "name": "Old Agent",
                "agent_id": "old-agent"
            }
        }
        collector.agent_tasks = {"old-agent": mock_task}

        # New configs don't include old-agent
        collector._load_agent_configs_from_db = AsyncMock(return_value={})

        await collector._refresh_agent_configs()

        # Check agent was removed
        assert "old-agent" not in collector.agent_configs
        assert "old-agent" not in collector.agent_tasks
        mock_task.cancel.assert_called_once()


class TestProcessMetrics:
    """Test _process_metrics method."""

    @pytest.mark.asyncio
    async def test_process_metrics_skips_empty(self):
        """Test that empty metrics are skipped."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_conn = AsyncMock()

        # Empty metrics dict
        await collector._process_metrics(mock_conn, "test-agent", {})

        # Should not call execute since no resourceMetrics
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_metrics_handles_gauge(self):
        """Test processing gauge metrics."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_conn = AsyncMock()

        metrics = {
            "resourceMetrics": [{
                "resource": {"attributes": []},
                "scopeMetrics": [{
                    "metrics": [{
                        "name": "cpu_usage",
                        "gauge": {
                            "dataPoints": [{
                                "asDouble": 0.75,
                                "attributes": []
                            }]
                        }
                    }]
                }]
            }]
        }

        await collector._process_metrics(mock_conn, "test-agent", metrics)

        # Should have called execute to insert metric
        assert mock_conn.execute.called


class TestStoreOTLPData:
    """Test store_otlp_data method."""

    @pytest.mark.asyncio
    async def test_store_otlp_data_inserts_raw_data(self):
        """Test that raw OTLP data is inserted."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        collector.pool = mock_pool

        metrics = {"resourceMetrics": []}
        traces = {"resourceSpans": []}
        logs = {"resourceLogs": []}

        await collector.store_otlp_data("test-agent", metrics, traces, logs)

        # Should have called execute at least once for raw data insert
        assert mock_conn.execute.called

        # First call should be the raw data insert
        first_call = mock_conn.execute.call_args_list[0]
        assert "INSERT INTO otlp_telemetry" in first_call.args[0]

    @pytest.mark.asyncio
    async def test_store_otlp_data_handles_none_signals(self):
        """Test storing when some signals are None."""
        from api.otlp_collector import OTLPCollector

        collector = OTLPCollector("postgresql://test@localhost/testdb")

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        collector.pool = mock_pool

        # Only metrics available
        await collector.store_otlp_data("test-agent", {"resourceMetrics": []}, None, None)

        assert mock_conn.execute.called
