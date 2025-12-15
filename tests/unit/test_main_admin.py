"""
Unit tests for main.py admin and log ingest endpoints.

Tests OAuth, configuration management, manager endpoints, and log ingestion.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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


class TestSessionManagement:
    """Tests for session management functions."""

    def test_create_session_returns_token(self):
        """create_session should return a session ID."""
        from main import OAuthUser, create_session

        user = OAuthUser(email="test@ciris.ai", name="Test User", hd="ciris.ai")
        session_id = create_session(user)

        assert session_id is not None
        assert len(session_id) > 20

    def test_create_session_stores_user_data(self):
        """create_session should store user data."""
        from main import OAuthUser, create_session, sessions

        user = OAuthUser(email="test@ciris.ai", name="Test User", hd="ciris.ai")
        session_id = create_session(user)

        assert session_id in sessions
        assert sessions[session_id]["user"]["email"] == "test@ciris.ai"

    def test_get_current_user_returns_none_for_missing_cookie(self):
        """get_current_user should return None without cookie."""
        from main import get_current_user

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None

        result = get_current_user(mock_request)
        assert result is None

    def test_get_current_user_returns_none_for_invalid_session(self):
        """get_current_user should return None for invalid session."""
        from main import get_current_user

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = "invalid-session-id"

        result = get_current_user(mock_request)
        assert result is None

    def test_get_current_user_returns_user_for_valid_session(self):
        """get_current_user should return user for valid session."""
        from main import OAuthUser, create_session, get_current_user

        user = OAuthUser(email="valid@ciris.ai", name="Valid User", hd="ciris.ai")
        session_id = create_session(user)

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = get_current_user(mock_request)
        assert result is not None
        assert result["email"] == "valid@ciris.ai"

    def test_get_current_user_expires_old_sessions(self):
        """get_current_user should expire old sessions."""
        from main import get_current_user, sessions

        # Create an expired session manually
        session_id = "expired-session-123"
        sessions[session_id] = {
            "user": {"email": "expired@ciris.ai", "name": "Expired User"},
            "created_at": (datetime.now(UTC) - timedelta(hours=48)).isoformat(),
            "expires_at": (datetime.now(UTC) - timedelta(hours=24)).isoformat(),
        }

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = get_current_user(mock_request)
        assert result is None
        assert session_id not in sessions

    def test_require_auth_raises_for_unauthenticated(self):
        """require_auth should raise HTTPException for unauthenticated users."""
        from main import require_auth

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            require_auth(mock_request)

        assert exc_info.value.status_code == 401


class TestOAuthRoutes:
    """Tests for OAuth endpoints."""

    @pytest.mark.asyncio
    async def test_oauth_login_mock_mode(self):
        """oauth_login should auto-authenticate in mock mode."""
        from main import oauth_login

        with patch("main.OAUTH_CLIENT_ID", "mock-client-id"):
            response = await oauth_login()

        assert response.status_code == 302
        assert "/lens/admin/" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_oauth_login_production_mode(self):
        """oauth_login should redirect to Google in production mode."""
        from main import oauth_login

        with patch("main.OAUTH_CLIENT_ID", "real-client-id"):
            response = await oauth_login()

        assert response.status_code == 307
        location = response.headers.get("location", "")
        assert "accounts.google.com" in location
        assert "client_id=real-client-id" in location

    @pytest.mark.asyncio
    async def test_auth_status_unauthenticated(self):
        """auth_status should return unauthenticated status."""
        from main import auth_status

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None

        result = await auth_status(mock_request)

        assert result["authenticated"] is False
        assert result["user"] is None

    @pytest.mark.asyncio
    async def test_auth_status_authenticated(self):
        """auth_status should return authenticated status."""
        from main import OAuthUser, auth_status, create_session

        user = OAuthUser(email="auth@ciris.ai", name="Auth User", hd="ciris.ai")
        session_id = create_session(user)

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        result = await auth_status(mock_request)

        assert result["authenticated"] is True
        assert result["user"]["email"] == "auth@ciris.ai"

    @pytest.mark.asyncio
    async def test_logout_clears_session(self):
        """logout should clear session."""
        from main import OAuthUser, create_session, logout, sessions

        user = OAuthUser(email="logout@ciris.ai", name="Logout User", hd="ciris.ai")
        session_id = create_session(user)

        mock_request = MagicMock()
        mock_request.cookies.get.return_value = session_id

        await logout(mock_request)

        assert session_id not in sessions


class TestConfigurationRoutes:
    """Tests for configuration management endpoints."""

    @pytest.mark.asyncio
    async def test_get_configurations(self):
        """get_configurations should return configs."""
        from main import get_configurations

        user = {"email": "admin@ciris.ai"}
        result = await get_configurations(user)

        assert "telemetry" in result
        assert "visibility" in result

    @pytest.mark.asyncio
    async def test_get_telemetry_config_default(self):
        """get_telemetry_config should return default for unknown agent."""
        from main import get_telemetry_config

        user = {"email": "admin@ciris.ai"}
        result = await get_telemetry_config("unknown-agent", user)

        assert result["agent_id"] == "unknown-agent"
        assert result["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_telemetry_config(self):
        """update_telemetry_config should update config."""
        from main import TelemetryConfig, telemetry_configs, update_telemetry_config

        user = {"email": "admin@ciris.ai"}
        config = TelemetryConfig(
            agent_id="test-agent",
            enabled=True,
            collection_interval=30
        )

        result = await update_telemetry_config("test-agent", config, user)

        assert result["status"] == "updated"
        assert "test-agent" in telemetry_configs

    @pytest.mark.asyncio
    async def test_patch_telemetry_config(self):
        """patch_telemetry_config should partially update config."""
        from main import patch_telemetry_config, telemetry_configs

        user = {"email": "admin@ciris.ai"}
        updates = {"enabled": True}

        result = await patch_telemetry_config("patch-agent", updates, user)

        assert result["status"] == "updated"
        assert telemetry_configs["patch-agent"]["enabled"] is True
        assert telemetry_configs["patch-agent"]["updated_by"] == "admin@ciris.ai"

    @pytest.mark.asyncio
    async def test_get_visibility_config_default(self):
        """get_visibility_config should return default for unknown agent."""
        from main import get_visibility_config

        user = {"email": "admin@ciris.ai"}
        result = await get_visibility_config("unknown-agent", user)

        assert result["agent_id"] == "unknown-agent"
        assert result["public_visible"] is False
        assert result["redact_pii"] is True

    @pytest.mark.asyncio
    async def test_update_visibility_config_enforces_pii_redaction(self):
        """update_visibility_config should always enforce PII redaction."""
        from main import VisibilityConfig, update_visibility_config, visibility_configs

        user = {"email": "admin@ciris.ai"}
        config = VisibilityConfig(
            agent_id="vis-agent",
            public_visible=True,
            redact_pii=False  # Try to disable - should be overridden
        )

        result = await update_visibility_config("vis-agent", config, user)

        assert result["status"] == "updated"
        assert visibility_configs["vis-agent"]["redact_pii"] is True  # Enforced

    @pytest.mark.asyncio
    async def test_patch_visibility_config_enforces_pii_redaction(self):
        """patch_visibility_config should enforce PII redaction."""
        from main import patch_visibility_config, visibility_configs

        user = {"email": "admin@ciris.ai"}
        updates = {"redact_pii": False}  # Try to disable

        await patch_visibility_config("patch-vis-agent", updates, user)

        assert visibility_configs["patch-vis-agent"]["redact_pii"] is True  # Enforced


class TestManagerRoutes:
    """Tests for manager management endpoints."""

    @pytest.mark.asyncio
    async def test_get_managers_empty_without_db(self):
        """get_managers should return empty list without DB."""
        from main import get_managers

        user = {"email": "admin@ciris.ai"}

        with patch("main.db_pool", None):
            result = await get_managers(user)

        assert result["managers"] == []

    @pytest.mark.asyncio
    async def test_get_managers_returns_list(self):
        """get_managers should return manager list."""
        from main import get_managers

        user = {"email": "admin@ciris.ai"}

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "id": 1,
                "name": "Test Manager",
                "url": "http://manager:8888",
                "description": "Test",
                "enabled": True,
                "last_seen": datetime.now(UTC),
                "last_error": None,
                "collection_interval_seconds": 30,
                "added_at": datetime.now(UTC)
            }
        ])

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))

        with patch("main.db_pool", mock_pool):
            result = await get_managers(user)

        assert len(result["managers"]) == 1
        assert result["managers"][0]["name"] == "Test Manager"

    @pytest.mark.asyncio
    async def test_add_manager_requires_db(self):
        """add_manager should require database."""
        from main import ManagerConfig, add_manager

        user = {"email": "admin@ciris.ai"}
        config = ManagerConfig(
            name="New Manager",
            url="http://manager:8888"
        )

        with patch("main.db_pool", None):
            with pytest.raises(HTTPException) as exc_info:
                await add_manager(config, user)

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_delete_manager_requires_collector(self):
        """delete_manager should require manager collector."""
        from main import delete_manager

        user = {"email": "admin@ciris.ai"}

        with patch("main.manager_collector", None):
            with pytest.raises(HTTPException) as exc_info:
                await delete_manager(1, user)

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_manager_agents_empty_without_db(self):
        """get_manager_agents should return empty without DB."""
        from main import get_manager_agents

        user = {"email": "admin@ciris.ai"}

        with patch("main.db_pool", None):
            result = await get_manager_agents(1, user)

        assert result["agents"] == []

    @pytest.mark.asyncio
    async def test_get_all_discovered_agents_empty_without_db(self):
        """get_all_discovered_agents should return empty without DB."""
        from main import get_all_discovered_agents

        user = {"email": "admin@ciris.ai"}

        with patch("main.db_pool", None):
            result = await get_all_discovered_agents(user)

        assert result["agents"] == []

    @pytest.mark.asyncio
    async def test_get_stats_empty_without_collector(self):
        """get_stats should return empty without collector."""
        from main import get_stats

        user = {"email": "admin@ciris.ai"}

        with patch("main.manager_collector", None):
            result = await get_stats(user)

        assert result["stats"] == {}


class TestAgentTokenRoutes:
    """Tests for agent token management endpoints."""

    @pytest.mark.asyncio
    async def test_get_agent_tokens(self):
        """get_agent_tokens should return token list."""
        from main import get_agent_tokens

        user = {"email": "admin@ciris.ai"}

        mock_manager = AsyncMock()
        mock_manager.get_configured_agents = AsyncMock(return_value=[
            {"agent_name": "datum", "url": "http://datum:8080"}
        ])

        with patch("main.token_manager", mock_manager):
            result = await get_agent_tokens(user)

        assert "agents" in result

    @pytest.mark.asyncio
    async def test_set_agent_token_success(self):
        """set_agent_token should update token."""
        from main import AgentTokenConfig, set_agent_token

        user = {"email": "admin@ciris.ai"}
        config = AgentTokenConfig(
            agent_name="test-agent",
            token="test-token",
            url="http://test:8080"
        )

        mock_manager = AsyncMock()
        mock_manager.set_agent_token = AsyncMock(return_value=True)

        with patch("main.token_manager", mock_manager):
            with patch("main.otlp_collector", None):
                result = await set_agent_token(config, user)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_set_agent_token_failure(self):
        """set_agent_token should handle failure."""
        from main import AgentTokenConfig, set_agent_token

        user = {"email": "admin@ciris.ai"}
        config = AgentTokenConfig(
            agent_name="test-agent",
            token="test-token",
            url="http://test:8080"
        )

        mock_manager = AsyncMock()
        mock_manager.set_agent_token = AsyncMock(return_value=False)

        with patch("main.token_manager", mock_manager):
            with pytest.raises(HTTPException) as exc_info:
                await set_agent_token(config, user)

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_remove_agent_token_success(self):
        """remove_agent_token should remove token."""
        from main import remove_agent_token

        user = {"email": "admin@ciris.ai"}

        mock_manager = AsyncMock()
        mock_manager.remove_agent_token = AsyncMock(return_value=True)

        with patch("main.token_manager", mock_manager):
            with patch("main.otlp_collector", None):
                result = await remove_agent_token("test-agent", user)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_remove_agent_token_not_found(self):
        """remove_agent_token should return 404 for unknown agent."""
        from main import remove_agent_token

        user = {"email": "admin@ciris.ai"}

        mock_manager = AsyncMock()
        mock_manager.remove_agent_token = AsyncMock(return_value=False)

        with patch("main.token_manager", mock_manager):
            with pytest.raises(HTTPException) as exc_info:
                await remove_agent_token("unknown-agent", user)

        assert exc_info.value.status_code == 404


class TestLogIngestRoutes:
    """Tests for log ingestion endpoints."""

    @pytest.mark.asyncio
    async def test_ingest_logs_requires_service(self):
        """ingest_logs should require log_ingest_service."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Bearer test-token"

        with patch("main.log_ingest_service", None):
            with pytest.raises(HTTPException) as exc_info:
                await ingest_logs(mock_request)

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_ingest_logs_requires_auth_header(self):
        """ingest_logs should require Authorization header."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get.return_value = ""

        mock_service = MagicMock()

        with patch("main.log_ingest_service", mock_service):
            with pytest.raises(HTTPException) as exc_info:
                await ingest_logs(mock_request)

        assert exc_info.value.status_code == 401
        assert "Authorization" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_ingest_logs_validates_token(self):
        """ingest_logs should validate token."""
        from main import ingest_logs

        mock_request = MagicMock()
        mock_request.headers.get.side_effect = lambda k, d="": {
            "Authorization": "Bearer invalid-token",
            "Content-Type": "application/json"
        }.get(k, d)

        mock_service = AsyncMock()
        mock_service.verify_token = AsyncMock(return_value=None)

        with patch("main.log_ingest_service", mock_service):
            with pytest.raises(HTTPException) as exc_info:
                await ingest_logs(mock_request)

        assert exc_info.value.status_code == 401
        assert "Invalid service token" in exc_info.value.detail
