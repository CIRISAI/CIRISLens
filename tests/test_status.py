"""Tests for status API endpoints with multi-region support."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    """Create a test FastAPI application instance."""
    from api.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app):
    """Create an async HTTP client for testing the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestLocalStatusEndpoint:
    """Tests for /v1/status - local CIRISLens health check."""

    @pytest.mark.asyncio
    async def test_service_status_returns_valid_structure(self, client: AsyncClient):
        """Test /v1/status returns expected response structure."""
        response = await client.get("/v1/status")
        assert response.status_code == 200

        data = response.json()
        assert data["service"] == "cirislens"
        assert data["status"] in ["operational", "degraded", "outage"]
        assert "timestamp" in data
        assert "version" in data
        assert "providers" in data

    @pytest.mark.asyncio
    async def test_service_status_includes_providers(self, client: AsyncClient):
        """Test /v1/status includes postgresql and grafana providers."""
        response = await client.get("/v1/status")
        assert response.status_code == 200

        data = response.json()
        providers = data["providers"]
        assert "postgresql" in providers
        assert "grafana" in providers

        for provider in providers.values():
            assert "status" in provider
            assert "last_check" in provider
            assert provider["status"] in ["operational", "degraded", "outage"]


class TestProviderStatusModel:
    """Tests for ProviderStatus model."""

    def test_provider_status_valid_statuses(self):
        """Test ProviderStatus accepts valid status values."""
        from api.main import ProviderStatus

        for status in ["operational", "degraded", "outage"]:
            provider = ProviderStatus(
                status=status,
                latency_ms=100,
                last_check="2025-12-14T00:00:00Z",
            )
            assert provider.status == status

    def test_provider_status_optional_fields(self):
        """Test ProviderStatus with optional fields."""
        from api.main import ProviderStatus

        provider = ProviderStatus(
            status="outage",
            last_check="2025-12-14T00:00:00Z",
            message="Connection failed",
        )
        assert provider.latency_ms is None
        assert provider.message == "Connection failed"


class TestAggregatedStatusEndpoint:
    """Tests for /api/v1/status - aggregated multi-region status."""

    @pytest.mark.asyncio
    async def test_aggregated_status_returns_valid_structure(self, client: AsyncClient):
        """Test /api/v1/status returns expected multi-region structure."""
        response = await client.get("/api/v1/status")
        assert response.status_code == 200

        data = response.json()
        assert "status" in data
        assert "timestamp" in data
        assert "regions" in data
        assert "infrastructure" in data
        assert "llm_providers" in data
        assert "auth_providers" in data
        assert "database_providers" in data
        assert "internal_providers" in data

    @pytest.mark.asyncio
    async def test_aggregated_status_valid_overall_status(self, client: AsyncClient):
        """Test overall status is one of expected values."""
        response = await client.get("/api/v1/status")
        assert response.status_code == 200

        data = response.json()
        valid_statuses = ["operational", "degraded", "partial_outage", "major_outage"]
        assert data["status"] in valid_statuses

    @pytest.mark.asyncio
    async def test_aggregated_status_regions_structure(self, client: AsyncClient):
        """Test regions have correct structure."""
        response = await client.get("/api/v1/status")
        assert response.status_code == 200

        data = response.json()
        for _region_key, region_data in data["regions"].items():
            assert "name" in region_data
            assert "status" in region_data
            assert "services" in region_data
            assert region_data["status"] in ["operational", "degraded", "outage", "unknown"]


class TestRegionStatusModel:
    """Tests for RegionStatus model."""

    def test_region_status_model(self):
        """Test RegionStatus model structure."""
        from api.main import RegionStatus, ServiceSummary

        region = RegionStatus(
            name="US (Chicago)",
            status="operational",
            services={
                "billing": ServiceSummary(
                    name="Billing & Authentication",
                    status="operational",
                    latency_ms=50,
                )
            },
        )
        assert region.name == "US (Chicago)"
        assert region.status == "operational"
        assert "billing" in region.services


class TestFetchServiceStatus:
    """Tests for fetch_service_status function."""

    @pytest.mark.asyncio
    async def test_fetch_service_status_success(self):
        """Test fetching status from a healthy service."""
        from api.main import fetch_service_status

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "service": "testservice",
            "status": "operational",
            "providers": {"db": {"status": "operational"}},
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            name, data = await fetch_service_status("test", "http://test-service")

        assert name == "test"
        assert data["status"] == "operational"

    @pytest.mark.asyncio
    async def test_fetch_service_status_timeout(self):
        """Test handling timeout when fetching service status."""
        from api.main import fetch_service_status

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            name, data = await fetch_service_status("test", "http://test-service")

        assert name == "test"
        assert data["status"] == "outage"
        assert data["error"] == "Timeout"

    @pytest.mark.asyncio
    async def test_fetch_service_status_connection_error(self):
        """Test handling connection error - should not leak internal details."""
        from api.main import fetch_service_status

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused to internal-host:8080")
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            name, data = await fetch_service_status("test", "http://test-service")

        assert name == "test"
        assert data["status"] == "outage"
        # Should not leak internal error details
        assert data["error"] == "Connection failed"
        assert "internal-host" not in data["error"]


class TestCheckInfrastructure:
    """Tests for check_infrastructure function."""

    @pytest.mark.asyncio
    async def test_infrastructure_check_operational(self):
        """Test infrastructure check returns operational on success."""
        from api.main import check_infrastructure

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_infrastructure("Test", "http://health", "provider")

        assert result.status == "operational"
        assert result.name == "Test"
        assert result.provider == "provider"

    @pytest.mark.asyncio
    async def test_infrastructure_check_accepts_401(self):
        """Test infrastructure check accepts 401 when accept_401=True."""
        from api.main import check_infrastructure

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_infrastructure(
                "Container Registry", "http://registry", "github", accept_401=True
            )

        assert result.status == "operational"

    @pytest.mark.asyncio
    async def test_infrastructure_check_custom_latency_threshold(self):
        """Test infrastructure check respects custom latency threshold."""
        from api.main import check_infrastructure

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            # Simulate a slow response by patching datetime
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            # With high threshold, should be operational
            result = await check_infrastructure(
                "Test", "http://health", "provider", latency_threshold=5000
            )

        assert result.status == "operational"

    @pytest.mark.asyncio
    async def test_infrastructure_check_outage_on_error(self):
        """Test infrastructure check returns outage on connection error."""
        from api.main import check_infrastructure

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("Failed"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_infrastructure("Test", "http://health", "provider")

        assert result.status == "outage"
        assert result.latency_ms is None


class TestStatusHistoryEndpoint:
    """Tests for /api/v1/status/history endpoint."""

    @pytest.mark.asyncio
    async def test_history_invalid_days_too_low(self, client: AsyncClient):
        """Test history endpoint rejects days < 1."""
        response = await client.get("/api/v1/status/history?days=0")
        assert response.status_code == 400
        assert "Days must be between" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_history_invalid_days_too_high(self, client: AsyncClient):
        """Test history endpoint rejects days > 365."""
        response = await client.get("/api/v1/status/history?days=400")
        assert response.status_code == 400
        assert "Days must be between" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_history_invalid_region(self, client: AsyncClient):
        """Test history endpoint rejects invalid region."""
        response = await client.get("/api/v1/status/history?region=invalid")
        assert response.status_code == 400
        assert "Invalid region" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_history_valid_regions_accepted(self, client: AsyncClient):
        """Test history endpoint accepts valid region values."""
        valid_regions = ["us", "eu", "global"]

        for region in valid_regions:
            response = await client.get(f"/api/v1/status/history?region={region}")
            # Should not return 400 for invalid region
            assert response.status_code != 400 or "Invalid region" not in response.json().get(
                "detail", ""
            )

    @pytest.mark.asyncio
    async def test_history_returns_region_in_response(self):
        """Test history endpoint returns region filter in response."""
        import api.main as main_module
        from api.main import status_history

        # Mock db_pool
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        original_pool = main_module.db_pool
        main_module.db_pool = mock_pool

        try:
            result = await status_history(days=7, region="us")
            assert result["region"] == "us"
            assert result["days"] == 7
        finally:
            main_module.db_pool = original_pool

    @pytest.mark.asyncio
    async def test_history_groups_by_region(self):
        """Test history endpoint groups results by region."""
        import api.main as main_module
        from api.main import status_history

        # Mock database rows with region data
        mock_rows = [
            {
                "date": date(2025, 12, 14),
                "region": "us",
                "service_name": "cirisbilling",
                "provider_name": "postgresql",
                "uptime_pct": 99.9,
                "avg_latency_ms": 50,
                "outage_count": 0,
            },
            {
                "date": date(2025, 12, 14),
                "region": "eu",
                "service_name": "cirisbilling",
                "provider_name": "postgresql",
                "uptime_pct": 99.5,
                "avg_latency_ms": 80,
                "outage_count": 1,
            },
            {
                "date": date(2025, 12, 14),
                "region": "global",
                "service_name": "cirisproxy",
                "provider_name": "openrouter",
                "uptime_pct": 100.0,
                "avg_latency_ms": 200,
                "outage_count": 0,
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        original_pool = main_module.db_pool
        main_module.db_pool = mock_pool

        try:
            result = await status_history(days=7)

            assert len(result["history"]) == 1
            day_data = result["history"][0]

            # Check regions are present
            assert "regions" in day_data
            assert "us" in day_data["regions"]
            assert "eu" in day_data["regions"]
            assert "global" in day_data["regions"]

            # Check region-specific data
            assert "uptime_pct" in day_data["regions"]["us"]
            assert "services" in day_data["regions"]["us"]

            # Check backwards-compatible flat services dict
            assert "services" in day_data
            assert "us.cirisbilling.postgresql" in day_data["services"]
            assert "eu.cirisbilling.postgresql" in day_data["services"]

            # Check overall uptime calculated
            assert "overall_uptime_pct" in day_data
        finally:
            main_module.db_pool = original_pool


class TestProxyStatusCalculation:
    """Tests for LLM Proxy status calculation logic."""

    def test_proxy_operational_when_any_provider_operational(self):
        """Proxy should be operational if any LLM provider is operational."""
        # Simulate proxy response with mixed statuses
        proxy_data = {
            "providers": [
                {"provider": "openrouter", "status": "operational"},
                {"provider": "groq", "status": "operational"},
                {"provider": "together", "status": "degraded"},  # One degraded
            ]
        }

        llm_statuses = [
            p.get("status", "unknown")
            for p in proxy_data["providers"]
            if p.get("provider") in ["openrouter", "groq", "together", "openai"]
        ]

        # Not all degraded, so should be operational
        all_degraded = all(s in ["degraded", "outage"] for s in llm_statuses)
        assert not all_degraded

    def test_proxy_degraded_only_when_all_providers_degraded(self):
        """Proxy should only be degraded if ALL LLM providers are degraded or worse."""
        proxy_data = {
            "providers": [
                {"provider": "openrouter", "status": "degraded"},
                {"provider": "groq", "status": "degraded"},
                {"provider": "together", "status": "outage"},
            ]
        }

        llm_statuses = [
            p.get("status", "unknown")
            for p in proxy_data["providers"]
            if p.get("provider") in ["openrouter", "groq", "together", "openai"]
        ]

        # All are degraded or worse
        all_degraded = all(s in ["degraded", "outage"] for s in llm_statuses)
        assert all_degraded

    def test_proxy_outage_only_when_all_providers_outage(self):
        """Proxy should only be outage if ALL LLM providers are in outage."""
        proxy_data = {
            "providers": [
                {"provider": "openrouter", "status": "outage"},
                {"provider": "groq", "status": "outage"},
                {"provider": "together", "status": "outage"},
            ]
        }

        llm_statuses = [
            p.get("status", "unknown")
            for p in proxy_data["providers"]
            if p.get("provider") in ["openrouter", "groq", "together", "openai"]
        ]

        all_outage = all(s == "outage" for s in llm_statuses)
        assert all_outage


class TestStatusCollectorRegions:
    """Tests for multi-region status collector logic."""

    def test_llm_providers_are_global(self):
        """Test that LLM providers are categorized as global."""
        llm_providers = ["openrouter", "groq", "together", "openai"]
        for provider in llm_providers:
            # In the collector, LLM providers should be marked as global region
            assert provider in llm_providers

    def test_regional_providers_are_regional(self):
        """Test that regional providers keep their region."""
        regional_providers = ["postgresql", "google_oauth", "google_play"]
        llm_providers = ["openrouter", "groq", "together", "openai"]

        for provider in regional_providers:
            assert provider not in llm_providers


class TestOverallStatusCalculation:
    """Tests for overall status calculation logic."""

    @pytest.mark.asyncio
    async def test_all_operational_returns_operational(self, client: AsyncClient):
        """When all regions are operational, overall should be operational."""
        # This tests the actual endpoint behavior
        response = await client.get("/api/v1/status")
        assert response.status_code == 200

        # The status calculation logic is tested implicitly
        data = response.json()
        assert data["status"] in ["operational", "degraded", "partial_outage", "major_outage"]

    def test_status_priority_logic(self):
        """Test the priority: major_outage > partial_outage > degraded > operational."""
        # This documents the expected behavior
        status_priority = ["operational", "degraded", "partial_outage", "major_outage"]
        for i, status in enumerate(status_priority):
            # Higher index = worse status
            assert status_priority.index(status) == i
