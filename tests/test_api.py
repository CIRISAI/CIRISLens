"""Basic API tests for CIRISLens."""

from __future__ import annotations

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


class TestHealthEndpoints:
    """Test health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test the health check endpoint returns OK."""
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_api_status(self, client: AsyncClient):
        """Test the API status endpoint."""
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data


class TestAuthEndpoints:
    """Test authentication endpoints."""

    @pytest.mark.asyncio
    async def test_auth_status_unauthenticated(self, client: AsyncClient):
        """Test auth status when not authenticated."""
        response = await client.get("/api/admin/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False
        assert data["user"] is None

    @pytest.mark.asyncio
    async def test_admin_redirect_unauthenticated(self, client: AsyncClient):
        """Test admin page redirects to login when not authenticated."""
        response = await client.get("/api/admin/", follow_redirects=False)
        assert response.status_code == 307
        assert "/api/admin/auth/login" in response.headers.get("location", "")


class TestLogIngestion:
    """Test log ingestion endpoints."""

    @pytest.mark.asyncio
    async def test_log_ingest_requires_auth(self, client: AsyncClient):
        """Test log ingestion requires authentication."""
        response = await client.post(
            "/api/v1/logs/ingest",
            json={"timestamp": "2024-01-01T00:00:00Z", "message": "test"},
        )
        # Should require Bearer token
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_log_ingest_invalid_token(self, client: AsyncClient):
        """Test log ingestion with invalid token."""
        response = await client.post(
            "/api/v1/logs/ingest",
            headers={"Authorization": "Bearer invalid-token"},
            json={"timestamp": "2024-01-01T00:00:00Z", "message": "test"},
        )
        assert response.status_code in (401, 403)


class TestOTLPEndpoints:
    """Test OTLP collector endpoints."""

    @pytest.mark.asyncio
    async def test_otlp_metrics_endpoint_exists(self, client: AsyncClient):
        """Test OTLP metrics endpoint exists."""
        # Empty POST should return 4xx, not 404
        response = await client.post("/v1/metrics", content=b"")
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_otlp_traces_endpoint_exists(self, client: AsyncClient):
        """Test OTLP traces endpoint exists."""
        response = await client.post("/v1/traces", content=b"")
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_otlp_logs_endpoint_exists(self, client: AsyncClient):
        """Test OTLP logs endpoint exists."""
        response = await client.post("/v1/logs", content=b"")
        assert response.status_code != 404
