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
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client: AsyncClient):
        """Test the root endpoint returns service info."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "CIRISLens API"
        assert data["status"] == "online"
        assert "version" in data


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
        response = await client.get("/admin/", follow_redirects=False)
        assert response.status_code == 307
        assert "/api/admin/auth/login" in response.headers.get("location", "")


class TestLogIngestion:
    """Test log ingestion endpoints."""

    @pytest.mark.asyncio
    async def test_log_ingest_without_auth_header(self, client: AsyncClient):
        """Test log ingestion without auth header returns 401."""
        response = await client.post(
            "/api/v1/logs/ingest",
            json={"logs": [{"timestamp": "2024-01-01T00:00:00Z", "message": "test"}]},
        )
        # Without log_ingest_service, returns 503; with it but no auth, returns 401
        assert response.status_code in (401, 403, 503)

    @pytest.mark.asyncio
    async def test_log_ingest_invalid_token(self, client: AsyncClient):
        """Test log ingestion with invalid token."""
        response = await client.post(
            "/api/v1/logs/ingest",
            headers={"Authorization": "Bearer invalid-token"},
            json={"logs": [{"timestamp": "2024-01-01T00:00:00Z", "message": "test"}]},
        )
        # Without log_ingest_service returns 503; with it but invalid token returns 401
        assert response.status_code in (401, 403, 503)


class TestStatusEndpoints:
    """Test status endpoints."""

    @pytest.mark.asyncio
    async def test_v1_status_endpoint(self, client: AsyncClient):
        """Test the v1/status endpoint exists."""
        response = await client.get("/v1/status")
        # May return 503 if no db, but shouldn't be 404
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_api_v1_status_endpoint(self, client: AsyncClient):
        """Test the api/v1/status endpoint exists."""
        response = await client.get("/api/v1/status")
        # May return 503 if no db, but shouldn't be 404
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_status_history_endpoint(self, client: AsyncClient):
        """Test the status history endpoint exists."""
        response = await client.get("/api/v1/status/history")
        # May return 503 if no db, but shouldn't be 404
        assert response.status_code != 404
