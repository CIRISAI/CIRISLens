"""
Unit tests for CIRISLens API routes in main.py

Tests session management, authentication, and core API endpoints.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestSessionManagement:
    """Test session creation and validation functions."""

    def test_create_session_returns_token(self):
        """Test that create_session returns a session token."""
        from api.main import OAuthUser, create_session, sessions

        user = OAuthUser(
            email="test@ciris.ai",
            name="Test User",
            picture=None,
            hd="ciris.ai"
        )

        session_id = create_session(user)

        assert session_id is not None
        assert len(session_id) > 20  # urlsafe tokens are long
        assert session_id in sessions
        assert sessions[session_id]["user"]["email"] == "test@ciris.ai"

        # Cleanup
        del sessions[session_id]

    def test_create_session_sets_expiry(self):
        """Test that sessions have proper expiry time."""
        from api.main import OAuthUser, create_session, sessions

        user = OAuthUser(
            email="test@ciris.ai",
            name="Test User"
        )

        session_id = create_session(user)
        session = sessions[session_id]

        expires_at = datetime.fromisoformat(session["expires_at"])
        now = datetime.now(UTC)

        # Expiry should be ~24 hours in the future
        assert expires_at > now
        assert expires_at < now + timedelta(hours=25)

        # Cleanup
        del sessions[session_id]

    def test_get_current_user_returns_none_without_session(self):
        """Test get_current_user returns None when no session exists."""
        from api.main import get_current_user

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None

        result = get_current_user(mock_request)
        assert result is None

    def test_get_current_user_returns_none_for_invalid_session(self):
        """Test get_current_user returns None for invalid session ID."""
        from api.main import get_current_user

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = "invalid-session-id"

        result = get_current_user(mock_request)
        assert result is None

    def test_get_current_user_returns_user_for_valid_session(self):
        """Test get_current_user returns user dict for valid session."""
        from api.main import OAuthUser, create_session, get_current_user, sessions

        user = OAuthUser(
            email="valid@ciris.ai",
            name="Valid User"
        )
        session_id = create_session(user)

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = get_current_user(mock_request)

        assert result is not None
        assert result["email"] == "valid@ciris.ai"
        assert result["name"] == "Valid User"

        # Cleanup
        del sessions[session_id]

    def test_get_current_user_removes_expired_session(self):
        """Test that expired sessions are removed."""
        from api.main import get_current_user, sessions

        # Create an expired session manually
        session_id = "test-expired-session"
        sessions[session_id] = {
            "user": {"email": "expired@ciris.ai", "name": "Expired User"},
            "created_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        }

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = get_current_user(mock_request)

        assert result is None
        assert session_id not in sessions


class TestRequireAuth:
    """Test the require_auth dependency."""

    def test_require_auth_raises_401_without_session(self):
        """Test that require_auth raises HTTPException when not authenticated."""
        from fastapi import HTTPException

        from api.main import require_auth

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            require_auth(mock_request)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_require_auth_returns_user_when_authenticated(self):
        """Test that require_auth returns user when authenticated."""
        from api.main import OAuthUser, create_session, require_auth, sessions

        user = OAuthUser(
            email="auth@ciris.ai",
            name="Auth User"
        )
        session_id = create_session(user)

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = require_auth(mock_request)

        assert result["email"] == "auth@ciris.ai"

        # Cleanup
        del sessions[session_id]


class TestProviderStatusModels:
    """Test ProviderStatus model."""

    def test_provider_status_creation(self):
        """Test ProviderStatus model can be created."""
        from api.main import ProviderStatus

        status = ProviderStatus(
            status="operational",
            latency_ms=50,
            last_check=datetime.now(UTC).isoformat() + "Z"
        )

        assert status.status == "operational"
        assert status.latency_ms == 50
        assert status.message is None

    def test_provider_status_with_message(self):
        """Test ProviderStatus with error message."""
        from api.main import ProviderStatus

        status = ProviderStatus(
            status="outage",
            latency_ms=None,
            last_check=datetime.now(UTC).isoformat() + "Z",
            message="Connection refused"
        )

        assert status.status == "outage"
        assert status.message == "Connection refused"


class TestCheckPostgresql:
    """Test PostgreSQL status check function."""

    @pytest.mark.asyncio
    async def test_check_postgresql_returns_outage_without_pool(self):
        """Test that check_postgresql returns outage when pool is None."""
        import api.main as main_module

        # Store original and set to None
        original_pool = main_module.db_pool
        main_module.db_pool = None

        try:
            result = await main_module.check_postgresql()
            assert result.status == "outage"
            assert result.message == "Database pool not initialized"
        finally:
            main_module.db_pool = original_pool

    @pytest.mark.asyncio
    async def test_check_postgresql_returns_operational_on_success(self):
        """Test that check_postgresql returns operational when DB responds."""
        import api.main as main_module

        # Create mock pool
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        original_pool = main_module.db_pool
        main_module.db_pool = mock_pool

        try:
            result = await main_module.check_postgresql()
            assert result.status in ["operational", "degraded"]
            assert result.latency_ms is not None
        finally:
            main_module.db_pool = original_pool

    @pytest.mark.asyncio
    async def test_check_postgresql_handles_timeout(self):
        """Test that check_postgresql handles timeouts gracefully."""
        import api.main as main_module

        mock_conn = AsyncMock()
        mock_conn.fetchval.side_effect = TimeoutError()

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        original_pool = main_module.db_pool
        main_module.db_pool = mock_pool

        try:
            result = await main_module.check_postgresql()
            assert result.status == "outage"
            assert result.message == "Connection timeout"
        finally:
            main_module.db_pool = original_pool

    @pytest.mark.asyncio
    async def test_check_postgresql_handles_exception(self):
        """Test that check_postgresql handles general exceptions."""
        import api.main as main_module

        mock_conn = AsyncMock()
        mock_conn.fetchval.side_effect = Exception("Connection refused")

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        original_pool = main_module.db_pool
        main_module.db_pool = mock_pool

        try:
            result = await main_module.check_postgresql()
            assert result.status == "outage"
            assert "Connection refused" in result.message
        finally:
            main_module.db_pool = original_pool


class TestCheckGrafana:
    """Test Grafana status check function."""

    @pytest.mark.asyncio
    async def test_check_grafana_returns_operational_on_200(self):
        """Test that check_grafana returns operational on HTTP 200."""
        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_grafana()

            assert result.status in ["operational", "degraded"]
            assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_check_grafana_returns_degraded_on_non_200(self):
        """Test that check_grafana returns degraded on non-200 status."""

        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_grafana()

            assert result.status == "degraded"
            assert "HTTP 503" in result.message

    @pytest.mark.asyncio
    async def test_check_grafana_handles_timeout(self):
        """Test that check_grafana handles timeout exceptions."""
        import httpx

        import api.main as main_module

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timeout")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_grafana()

            assert result.status == "outage"
            assert result.message == "Connection timeout"


class TestFetchServiceStatus:
    """Test fetch_service_status function."""

    @pytest.mark.asyncio
    async def test_fetch_service_status_success(self):
        """Test successful fetch from a service."""
        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "operational", "version": "1.0.0"}

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            name, data = await main_module.fetch_service_status("billing", "http://test")

            assert name == "billing"
            assert data["status"] == "operational"

    @pytest.mark.asyncio
    async def test_fetch_service_status_non_200(self):
        """Test fetch with non-200 response."""
        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            name, data = await main_module.fetch_service_status("proxy", "http://test")

            assert name == "proxy"
            assert data["status"] == "degraded"
            assert "HTTP 503" in data["error"]

    @pytest.mark.asyncio
    async def test_fetch_service_status_timeout(self):
        """Test fetch with timeout."""
        import httpx

        import api.main as main_module

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timeout")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            name, data = await main_module.fetch_service_status("manager", "http://test")

            assert name == "manager"
            assert data["status"] == "outage"
            assert data["error"] == "Timeout"

    @pytest.mark.asyncio
    async def test_fetch_service_status_connection_error(self):
        """Test fetch with connection error - shouldn't leak internal details."""
        import api.main as main_module

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = ConnectionError("Internal network error at 192.168.1.1")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            name, data = await main_module.fetch_service_status("test", "http://test")

            assert data["status"] == "outage"
            # Should not expose internal error details
            assert data["error"] == "Connection failed"
            assert "192.168.1.1" not in data["error"]


class TestCheckInfrastructure:
    """Test check_infrastructure function."""

    @pytest.mark.asyncio
    async def test_check_infrastructure_operational(self):
        """Test check_infrastructure returns operational on success."""
        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_infrastructure(
                "Test Server", "http://test/health", "vultr"
            )

            assert result.name == "Test Server"
            assert result.provider == "vultr"
            assert result.status in ["operational", "degraded"]

    @pytest.mark.asyncio
    async def test_check_infrastructure_accepts_401_when_configured(self):
        """Test that 401 is accepted when accept_401=True."""
        import api.main as main_module

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_infrastructure(
                "Container Registry", "http://ghcr.io/v2/", "github",
                accept_401=True
            )

            # 401 should be treated as operational when accept_401=True
            assert result.status in ["operational", "degraded"]

    @pytest.mark.asyncio
    async def test_check_infrastructure_outage_on_exception(self):
        """Test check_infrastructure returns outage on exception."""
        import api.main as main_module

        with patch("api.main.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection failed")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await main_module.check_infrastructure(
                "Test", "http://test", "test-provider"
            )

            assert result.status == "outage"
            assert result.latency_ms is None


class TestAPIEndpoints:
    """Test HTTP API endpoints."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        """Test root endpoint returns service info."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "CIRISLens API"
        assert data["status"] == "online"

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        """Test health endpoint."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_admin_redirects_without_auth(self, client):
        """Test admin page redirects when not authenticated."""
        response = await client.get("/admin/", follow_redirects=False)
        assert response.status_code == 307
        assert "/api/admin/auth/login" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_auth_status_unauthenticated(self, client):
        """Test auth status when not authenticated."""
        response = await client.get("/api/admin/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is False
        assert data["user"] is None


class TestOAuthUserModel:
    """Test OAuthUser pydantic model."""

    def test_oauth_user_creation(self):
        """Test OAuthUser model creation."""
        from api.main import OAuthUser

        user = OAuthUser(
            email="test@ciris.ai",
            name="Test User",
            picture="https://example.com/photo.jpg",
            hd="ciris.ai"
        )

        assert user.email == "test@ciris.ai"
        assert user.name == "Test User"
        assert user.picture == "https://example.com/photo.jpg"
        assert user.hd == "ciris.ai"

    def test_oauth_user_optional_fields(self):
        """Test OAuthUser with optional fields."""
        from api.main import OAuthUser

        user = OAuthUser(
            email="test@example.com",
            name="Test"
        )

        assert user.picture is None
        assert user.hd is None

    def test_oauth_user_email_validation(self):
        """Test OAuthUser email validation."""
        from pydantic import ValidationError

        from api.main import OAuthUser

        with pytest.raises(ValidationError):
            OAuthUser(email="not-an-email", name="Test")


class TestConfigModels:
    """Test configuration pydantic models."""

    def test_telemetry_config_defaults(self):
        """Test TelemetryConfig default values."""
        from api.main import TelemetryConfig

        config = TelemetryConfig(agent_id="test-agent")

        assert config.agent_id == "test-agent"
        assert config.enabled is False
        assert config.collection_interval == 60
        assert config.metrics_enabled is True
        assert config.traces_enabled is True
        assert config.logs_enabled is True

    def test_visibility_config_defaults(self):
        """Test VisibilityConfig default values."""
        from api.main import VisibilityConfig

        config = VisibilityConfig(agent_id="test-agent")

        assert config.public_visible is False
        assert config.show_metrics is True
        assert config.show_traces is False
        assert config.redact_pii is True

    def test_manager_config_creation(self):
        """Test ManagerConfig model."""
        from api.main import ManagerConfig

        config = ManagerConfig(
            name="Production Manager",
            url="https://manager.ciris.ai",
            description="Main production manager",
            auth_token="secret-token",
            collection_interval_seconds=60,
            enabled=True
        )

        assert config.name == "Production Manager"
        assert config.url == "https://manager.ciris.ai"
        assert config.collection_interval_seconds == 60

    def test_manager_update_partial(self):
        """Test ManagerUpdate model allows partial updates."""
        from api.main import ManagerUpdate

        update = ManagerUpdate(name="New Name")

        assert update.name == "New Name"
        assert update.url is None
        assert update.enabled is None
