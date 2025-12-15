"""
Extended unit tests for OTLPCollector class.

Tests agent config loading, metric/trace/log processing, and health checks.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))

from otlp_collector import OTLPCollector


class AsyncContextManagerMock:
    """Helper for async context managers."""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, *args):
        return None


class TestLoadAgentConfigsFromEnv:
    """Tests for _load_agent_configs_from_env method."""

    def test_loads_configs_from_environment(self):
        """Should load agent configs from environment variables."""
        collector = OTLPCollector("postgresql://test")

        with patch.dict("os.environ", {
            "AGENT_DATUM_TOKEN": "test-token",
            "AGENT_DATUM_URL": "http://datum:8080"
        }):
            configs = collector._load_agent_configs_from_env()

        assert "env_datum" in configs
        assert configs["env_datum"]["url"] == "http://datum:8080"
        assert configs["env_datum"]["token"] == "test-token"

    def test_skips_missing_url(self):
        """Should skip agents without URL."""
        collector = OTLPCollector("postgresql://test")

        with patch.dict("os.environ", {
            "AGENT_NOURL_TOKEN": "test-token"
        }, clear=True):
            configs = collector._load_agent_configs_from_env()

        assert "env_nourl" not in configs

    def test_strips_trailing_slash_from_url(self):
        """Should strip trailing slash from URL."""
        collector = OTLPCollector("postgresql://test")

        with patch.dict("os.environ", {
            "AGENT_TEST_TOKEN": "token",
            "AGENT_TEST_URL": "http://test:8080/"
        }):
            configs = collector._load_agent_configs_from_env()

        assert configs["env_test"]["url"] == "http://test:8080"


class TestLoadAgentConfigsFromDb:
    """Tests for _load_agent_configs_from_db method."""

    @pytest.mark.asyncio
    async def test_loads_active_agents(self):
        """Should load active agents from database."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "agent_id": "datum",
                "agent_name": "Datum",
                "api_port": 8080,
                "status": "running",
                "deployment": "docker",
                "container_name": "datum-agent",
                "manager_url": "http://manager:8888",
                "manager_token": "mgr-token"
            }
        ])
        mock_conn.fetchval = AsyncMock(return_value=0)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        with patch.dict("os.environ", {"DOCKER_HOST_IP": "172.17.0.1"}, clear=True):
            configs = await collector._load_agent_configs_from_db()

        assert "datum" in configs
        assert configs["datum"]["name"] == "Datum"

    @pytest.mark.asyncio
    async def test_uses_env_url_if_available(self):
        """Should prefer environment URL over constructed URL."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "agent_id": "datum",
                "agent_name": "Datum",
                "api_port": 8080,
                "status": "running",
                "deployment": "docker",
                "container_name": None,
                "manager_url": "http://manager:8888",
                "manager_token": None
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        with patch.dict("os.environ", {
            "AGENT_DATUM_URL": "https://datum.ciris.ai"
        }):
            configs = await collector._load_agent_configs_from_db()

        assert configs["datum"]["url"] == "https://datum.ciris.ai"

    @pytest.mark.asyncio
    async def test_handles_empty_result(self):
        """Should handle no agents gracefully."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)  # No historical agents

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        configs = await collector._load_agent_configs_from_db()

        assert configs == {}

    @pytest.mark.asyncio
    async def test_fallback_to_stale_agents(self):
        """Should use stale agents as fallback when current agents unavailable."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        # First call returns empty (current agents)
        # Second call returns stale agents
        mock_conn.fetch = AsyncMock(side_effect=[
            [],  # Current agents query
            [{  # Fallback stale agents query
                "agent_id": "stale-agent",
                "agent_name": "Stale Agent",
                "api_port": 8080,
                "container_name": None,
                "manager_url": "http://manager:8888",
                "manager_token": "token"
            }]
        ])
        mock_conn.fetchval = AsyncMock(return_value=5)  # Has historical agents

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        with patch.dict("os.environ", {"DOCKER_HOST_IP": "172.17.0.1"}):
            configs = await collector._load_agent_configs_from_db()

        assert "stale-agent" in configs
        assert "[STALE]" in configs["stale-agent"]["name"]

    @pytest.mark.asyncio
    async def test_handles_db_exception(self):
        """Should return empty config on database error."""
        collector = OTLPCollector("postgresql://test")

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(side_effect=Exception("DB error"))
        collector.pool = mock_pool

        configs = await collector._load_agent_configs_from_db()

        assert configs == {}


class TestProcessMetrics:
    """Tests for _process_metrics method."""

    @pytest.mark.asyncio
    async def test_processes_gauge_metrics(self):
        """Should process gauge metrics correctly."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        metrics = {
            "resourceMetrics": [{
                "resource": {"attributes": []},
                "scopeMetrics": [{
                    "metrics": [{
                        "name": "test_gauge",
                        "gauge": {
                            "dataPoints": [{
                                "asDouble": 42.5,
                                "timeUnixNano": 1705320000000000000,
                                "attributes": [
                                    {"key": "label1", "value": {"stringValue": "value1"}}
                                ]
                            }]
                        }
                    }]
                }]
            }]
        }

        await collector._process_metrics(mock_conn, "test-agent", metrics)

        mock_conn.execute.assert_called()
        call_args = mock_conn.execute.call_args[0]
        assert "INSERT INTO agent_metrics" in call_args[0]
        assert call_args[1] == "test-agent"
        assert call_args[2] == "test_gauge"
        assert call_args[3] == 42.5

    @pytest.mark.asyncio
    async def test_processes_sum_metrics(self):
        """Should process sum metrics correctly."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        metrics = {
            "resourceMetrics": [{
                "resource": {"attributes": []},
                "scopeMetrics": [{
                    "metrics": [{
                        "name": "test_counter",
                        "sum": {
                            "dataPoints": [{
                                "asInt": 100,
                                "timeUnixNano": 1705320000000000000,
                                "attributes": []
                            }]
                        }
                    }]
                }]
            }]
        }

        await collector._process_metrics(mock_conn, "test-agent", metrics)

        mock_conn.execute.assert_called()
        call_args = mock_conn.execute.call_args[0]
        assert call_args[3] == 100.0

    @pytest.mark.asyncio
    async def test_handles_attribute_types(self):
        """Should handle different attribute value types."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        metrics = {
            "resourceMetrics": [{
                "scopeMetrics": [{
                    "metrics": [{
                        "name": "test_metric",
                        "gauge": {
                            "dataPoints": [{
                                "asDouble": 1.0,
                                "timeUnixNano": 1705320000000000000,
                                "attributes": [
                                    {"key": "string_attr", "value": {"stringValue": "str"}},
                                    {"key": "int_attr", "value": {"intValue": 42}},
                                    {"key": "bool_attr", "value": {"boolValue": True}},
                                ]
                            }]
                        }
                    }]
                }]
            }]
        }

        await collector._process_metrics(mock_conn, "test-agent", metrics)

        # Should have processed without error
        mock_conn.execute.assert_called()

    @pytest.mark.asyncio
    async def test_skips_missing_resourceMetrics(self):
        """Should skip metrics without resourceMetrics."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        await collector._process_metrics(mock_conn, "test-agent", {})

        mock_conn.execute.assert_not_called()


class TestProcessTraces:
    """Tests for _process_traces method."""

    @pytest.mark.asyncio
    async def test_processes_traces(self):
        """Should process trace spans correctly."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        traces = {
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "traceId": "abc123",
                        "spanId": "def456",
                        "parentSpanId": None,
                        "name": "test-operation",
                        "startTimeUnixNano": 1705320000000000000,
                        "endTimeUnixNano": 1705320001000000000,
                        "attributes": [],
                        "events": [],
                        "status": {"code": "OK"}
                    }]
                }]
            }]
        }

        await collector._process_traces(mock_conn, "test-agent", traces)

        mock_conn.execute.assert_called()
        call_args = mock_conn.execute.call_args[0]
        assert "INSERT INTO agent_traces" in call_args[0]
        assert call_args[1] == "test-agent"
        assert call_args[2] == "abc123"

    @pytest.mark.asyncio
    async def test_handles_string_timestamps(self):
        """Should handle string timestamps in traces."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        traces = {
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "traceId": "abc123",
                        "spanId": "def456",
                        "name": "test-operation",
                        "startTimeUnixNano": "1705320000000000000",  # String
                        "endTimeUnixNano": "1705320001000000000",   # String
                        "attributes": [],
                        "events": []
                    }]
                }]
            }]
        }

        await collector._process_traces(mock_conn, "test-agent", traces)

        mock_conn.execute.assert_called()

    @pytest.mark.asyncio
    async def test_skips_missing_resourceSpans(self):
        """Should skip traces without resourceSpans."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        await collector._process_traces(mock_conn, "test-agent", {})

        mock_conn.execute.assert_not_called()


class TestProcessLogs:
    """Tests for _process_logs method."""

    @pytest.mark.asyncio
    async def test_processes_logs(self):
        """Should process log records correctly."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        logs = {
            "resourceLogs": [{
                "scopeLogs": [{
                    "logRecords": [{
                        "timeUnixNano": 1705320000000000000,
                        "severityNumber": 9,  # INFO
                        "body": {"stringValue": "Test log message"},
                        "traceId": "trace123",
                        "spanId": "span456",
                        "attributes": []
                    }]
                }]
            }]
        }

        await collector._process_logs(mock_conn, "test-agent", logs)

        mock_conn.execute.assert_called()
        call_args = mock_conn.execute.call_args[0]
        assert "INSERT INTO agent_logs" in call_args[0]
        assert call_args[1] == "test-agent"
        assert call_args[3] == "INFO"
        assert call_args[4] == "Test log message"

    @pytest.mark.asyncio
    async def test_maps_severity_levels(self):
        """Should map severity numbers to levels correctly."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        # Test different severity levels
        severity_tests = [
            (5, "DEBUG"),
            (9, "INFO"),
            (13, "WARNING"),
            (17, "ERROR"),
            (21, "CRITICAL"),
            (99, "INFO"),  # Unknown defaults to INFO
        ]

        for severity_num, expected_level in severity_tests:
            mock_conn.execute.reset_mock()

            logs = {
                "resourceLogs": [{
                    "scopeLogs": [{
                        "logRecords": [{
                            "timeUnixNano": 1705320000000000000,
                            "severityNumber": severity_num,
                            "body": {"stringValue": "Test"},
                            "attributes": []
                        }]
                    }]
                }]
            }

            await collector._process_logs(mock_conn, "test-agent", logs)

            call_args = mock_conn.execute.call_args[0]
            assert call_args[3] == expected_level, f"Severity {severity_num} should map to {expected_level}"

    @pytest.mark.asyncio
    async def test_skips_missing_resourceLogs(self):
        """Should skip logs without resourceLogs."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        await collector._process_logs(mock_conn, "test-agent", {})

        mock_conn.execute.assert_not_called()


class TestStoreOtlpData:
    """Tests for store_otlp_data method."""

    @pytest.mark.asyncio
    async def test_stores_raw_data(self):
        """Should store raw OTLP data."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.store_otlp_data(
            "test-agent",
            metrics={"resourceMetrics": []},
            traces=None,
            logs=None
        )

        # Should have inserted into otlp_telemetry
        call_args = mock_conn.execute.call_args_list[0][0]
        assert "INSERT INTO otlp_telemetry" in call_args[0]

    @pytest.mark.asyncio
    async def test_handles_processing_errors(self):
        """Should handle errors in processing gracefully."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        # First call succeeds (raw insert), subsequent calls fail
        mock_conn.execute = AsyncMock(side_effect=[
            None,  # Raw insert succeeds
            Exception("Process error"),  # Metric processing fails
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        # Should not raise even if processing fails
        await collector.store_otlp_data(
            "test-agent",
            metrics={"resourceMetrics": [{"scopeMetrics": [{"metrics": []}]}]},
            traces=None,
            logs=None
        )


class TestGetAgentHealth:
    """Tests for get_agent_health method."""

    @pytest.mark.asyncio
    async def test_returns_healthy_status(self):
        """Should return healthy when metrics present and no errors."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"metric_count": 10, "last_metric": datetime.now(UTC)},
            {"error_count": 0}
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        health = await collector.get_agent_health("test-agent")

        assert health["healthy"] is True
        assert health["metric_count"] == 10
        assert health["recent_errors"] == 0

    @pytest.mark.asyncio
    async def test_returns_unhealthy_with_errors(self):
        """Should return unhealthy when there are recent errors."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"metric_count": 5, "last_metric": datetime.now(UTC)},
            {"error_count": 3}
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        health = await collector.get_agent_health("test-agent")

        assert health["healthy"] is False
        assert health["recent_errors"] == 3

    @pytest.mark.asyncio
    async def test_returns_unhealthy_with_no_metrics(self):
        """Should return unhealthy when no recent metrics."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"metric_count": 0, "last_metric": None},
            {"error_count": 0}
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        health = await collector.get_agent_health("test-agent")

        assert health["healthy"] is False
        assert health["metric_count"] == 0


class TestStoreCollectionError:
    """Tests for store_collection_error method."""

    @pytest.mark.asyncio
    async def test_stores_error(self):
        """Should store collection error in database."""
        collector = OTLPCollector("postgresql://test")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
        collector.pool = mock_pool

        await collector.store_collection_error("test-agent", "Connection refused")

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert "INSERT INTO collection_errors" in call_args[0]
        assert call_args[1] == "test-agent"
        assert call_args[2] == "Connection refused"


class TestFetchOtlpSignal:
    """Tests for _fetch_otlp_signal method."""

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self):
        """Should return JSON data on successful fetch."""
        collector = OTLPCollector("postgresql://test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"data": "test"})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await collector._fetch_otlp_signal(
            mock_client, "http://test/metrics", {"Authorization": "Bearer token"}
        )

        assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_returns_none_on_non_200(self):
        """Should return None on non-200 response."""
        collector = OTLPCollector("postgresql://test")

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await collector._fetch_otlp_signal(
            mock_client, "http://test/metrics", {}
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """Should return None on exception."""
        collector = OTLPCollector("postgresql://test")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        result = await collector._fetch_otlp_signal(
            mock_client, "http://test/metrics", {}
        )

        assert result is None
