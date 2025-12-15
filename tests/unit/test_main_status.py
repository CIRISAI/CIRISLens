"""
Unit tests for main.py status endpoints and helper functions.

Tests check_postgresql, check_grafana, service_status, aggregated_status, status_history.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


class TestCheckPostgresql:
    """Tests for check_postgresql function."""

    @pytest.mark.asyncio
    async def test_operational_when_fast(self):
        """Should return operational when query is fast."""
        from main import check_postgresql

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await check_postgresql()

        assert result.status == "operational"
        assert result.latency_ms is not None
        assert result.latency_ms < 1000

    @pytest.mark.asyncio
    async def test_outage_when_no_pool(self):
        """Should return outage when pool is None."""
        from main import check_postgresql

        with patch("main.db_pool", None):
            result = await check_postgresql()

        assert result.status == "outage"
        assert result.message == "Database pool not initialized"

    @pytest.mark.asyncio
    async def test_outage_on_timeout(self):
        """Should return outage on timeout."""
        from main import check_postgresql

        mock_conn = AsyncMock()

        async def slow_query(*args, **kwargs):
            await asyncio.sleep(10)
            return 1

        mock_conn.fetchval = slow_query

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            with patch("asyncio.wait_for", side_effect=TimeoutError()):
                result = await check_postgresql()

        assert result.status == "outage"
        assert result.latency_ms == 5000
        assert result.message == "Connection timeout"

    @pytest.mark.asyncio
    async def test_outage_on_exception(self):
        """Should return outage on exception."""
        from main import check_postgresql

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=Exception("DB connection error"))

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await check_postgresql()

        assert result.status == "outage"
        assert "DB connection error" in result.message


class TestCheckGrafana:
    """Tests for check_grafana function."""

    @pytest.mark.asyncio
    async def test_operational_when_healthy(self):
        """Should return operational when Grafana is healthy."""
        from main import check_grafana

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_grafana()

        assert result.status == "operational"
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_degraded_on_non_200(self):
        """Should return degraded on non-200 response."""
        from main import check_grafana

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_grafana()

        assert result.status == "degraded"
        assert "HTTP 503" in result.message

    @pytest.mark.asyncio
    async def test_outage_on_timeout(self):
        """Should return outage on timeout."""
        from main import check_grafana

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_grafana()

        assert result.status == "outage"
        assert result.latency_ms == 5000
        assert result.message == "Connection timeout"

    @pytest.mark.asyncio
    async def test_outage_on_connection_error(self):
        """Should return outage on connection error."""
        from main import check_grafana

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_grafana()

        assert result.status == "outage"
        assert "Connection refused" in result.message


class TestFetchServiceStatus:
    """Tests for fetch_service_status function."""

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self):
        """Should return service data on success."""
        from main import fetch_service_status

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"status": "operational"})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            name, data = await fetch_service_status("billing", "http://billing:8000")

        assert name == "billing"
        assert data["status"] == "operational"

    @pytest.mark.asyncio
    async def test_returns_degraded_on_non_200(self):
        """Should return degraded on non-200 response."""
        from main import fetch_service_status

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            name, data = await fetch_service_status("proxy", "http://proxy:8000")

        assert name == "proxy"
        assert data["status"] == "degraded"
        assert "HTTP 500" in data["error"]

    @pytest.mark.asyncio
    async def test_returns_outage_on_timeout(self):
        """Should return outage on timeout."""
        from main import fetch_service_status

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            name, data = await fetch_service_status("billing", "http://billing:8000")

        assert name == "billing"
        assert data["status"] == "outage"
        assert data["error"] == "Timeout"

    @pytest.mark.asyncio
    async def test_returns_outage_on_exception(self):
        """Should return outage on exception without leaking details."""
        from main import fetch_service_status

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Internal error details"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            name, data = await fetch_service_status("billing", "http://billing:8000")

        assert name == "billing"
        assert data["status"] == "outage"
        assert data["error"] == "Connection failed"  # No internal details leaked


class TestCheckInfrastructure:
    """Tests for check_infrastructure function."""

    @pytest.mark.asyncio
    async def test_operational_when_healthy(self):
        """Should return operational for fast response."""
        from main import check_infrastructure

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_infrastructure("US Server", "http://example.com", "vultr")

        assert result.status == "operational"
        assert result.name == "US Server"
        assert result.provider == "vultr"

    @pytest.mark.asyncio
    async def test_accepts_401_when_configured(self):
        """Should accept 401 responses when accept_401=True."""
        from main import check_infrastructure

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_infrastructure(
                "Registry", "http://ghcr.io/v2/", "github", accept_401=True
            )

        assert result.status == "operational"

    @pytest.mark.asyncio
    async def test_outage_on_exception(self):
        """Should return outage on exception."""
        from main import check_infrastructure

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_infrastructure("Server", "http://example.com", "test")

        assert result.status == "outage"
        assert result.latency_ms is None


class TestServiceStatus:
    """Tests for service_status endpoint."""

    @pytest.mark.asyncio
    async def test_returns_service_status(self):
        """Should return service status with providers."""
        from main import ProviderStatus, service_status

        mock_pg = ProviderStatus(
            status="operational",
            latency_ms=10,
            last_check=datetime.now(UTC).isoformat()
        )
        mock_grafana = ProviderStatus(
            status="operational",
            latency_ms=50,
            last_check=datetime.now(UTC).isoformat()
        )

        with patch("main.check_postgresql", return_value=mock_pg):
            with patch("main.check_grafana", return_value=mock_grafana):
                result = await service_status()

        assert result.service == "cirislens"
        assert result.status == "operational"
        assert "postgresql" in result.providers
        assert "grafana" in result.providers

    @pytest.mark.asyncio
    async def test_returns_outage_if_any_provider_down(self):
        """Should return outage if any provider is down."""
        from main import ProviderStatus, service_status

        mock_pg = ProviderStatus(
            status="outage",
            latency_ms=None,
            last_check=datetime.now(UTC).isoformat(),
            message="Connection failed"
        )
        mock_grafana = ProviderStatus(
            status="operational",
            latency_ms=50,
            last_check=datetime.now(UTC).isoformat()
        )

        with patch("main.check_postgresql", return_value=mock_pg):
            with patch("main.check_grafana", return_value=mock_grafana):
                result = await service_status()

        assert result.status == "outage"

    @pytest.mark.asyncio
    async def test_returns_degraded_if_any_provider_degraded(self):
        """Should return degraded if any provider is degraded."""
        from main import ProviderStatus, service_status

        mock_pg = ProviderStatus(
            status="degraded",
            latency_ms=1500,
            last_check=datetime.now(UTC).isoformat()
        )
        mock_grafana = ProviderStatus(
            status="operational",
            latency_ms=50,
            last_check=datetime.now(UTC).isoformat()
        )

        with patch("main.check_postgresql", return_value=mock_pg):
            with patch("main.check_grafana", return_value=mock_grafana):
                result = await service_status()

        assert result.status == "degraded"


class TestStatusHistory:
    """Tests for status_history endpoint."""

    @pytest.mark.asyncio
    async def test_validates_days_range(self):
        """Should reject invalid days parameter."""
        from fastapi import HTTPException

        from main import status_history

        with pytest.raises(HTTPException) as exc_info:
            await status_history(days=0)
        assert exc_info.value.status_code == 400

        with pytest.raises(HTTPException) as exc_info:
            await status_history(days=400)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_validates_region(self):
        """Should reject invalid region parameter."""
        from fastapi import HTTPException

        from main import status_history

        with pytest.raises(HTTPException) as exc_info:
            await status_history(days=30, region="invalid")
        assert exc_info.value.status_code == 400
        assert "Invalid region" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_returns_503_without_db(self):
        """Should return 503 when database not available."""
        from fastapi import HTTPException

        from main import status_history

        with patch("main.db_pool", None):
            with pytest.raises(HTTPException) as exc_info:
                await status_history(days=30)
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_returns_history_data(self):
        """Should return history data when available."""
        from main import status_history

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "date": datetime(2024, 1, 15).date(),
                "region": "us",
                "service_name": "cirisbilling",
                "provider_name": "postgresql",
                "uptime_pct": 99.9,
                "avg_latency_ms": 25,
                "outage_count": 0
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await status_history(days=7)

        assert result["days"] == 7
        assert "history" in result

    @pytest.mark.asyncio
    async def test_filters_by_region(self):
        """Should filter history by region."""
        from main import status_history

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await status_history(days=7, region="us")

        assert result["region"] == "us"
        # Should have used parameterized query with region
        call_args = mock_conn.fetch.call_args
        assert "us" in call_args[0]
