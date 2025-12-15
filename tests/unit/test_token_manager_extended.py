"""
Extended unit tests for TokenManager class.

Tests token management, .env file operations, and validation.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))

from token_manager import TokenManager


class TestTokenManagerInit:
    """Tests for TokenManager initialization."""

    def test_init_default_paths(self):
        """Should use default paths."""
        manager = TokenManager()

        assert manager.env_file_path == Path(".env")
        assert manager.tokens_metadata_file == Path(".tokens_metadata.json")

    def test_init_custom_env_path(self):
        """Should accept custom env file path."""
        manager = TokenManager(env_file_path="/custom/.env")

        assert manager.env_file_path == Path("/custom/.env")

    def test_init_loads_existing_metadata(self):
        """Should load existing metadata file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_file = Path(tmpdir) / ".tokens_metadata.json"
            metadata_file.write_text(json.dumps({
                "test_agent": {"last_updated": "2024-01-15", "updated_by": "admin"}
            }))

            manager = TokenManager()
            manager.tokens_metadata_file = metadata_file
            manager._load_metadata()

            assert "test_agent" in manager.metadata
            assert manager.metadata["test_agent"]["updated_by"] == "admin"

    def test_init_empty_metadata_if_no_file(self):
        """Should have empty metadata if file doesn't exist."""
        manager = TokenManager()
        manager.tokens_metadata_file = Path("/nonexistent/.tokens_metadata.json")
        manager._load_metadata()

        assert manager.metadata == {}


class TestSaveMetadata:
    """Tests for _save_metadata method."""

    def test_saves_metadata_to_file(self):
        """Should save metadata to JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {"agent1": {"last_updated": "2024-01-15"}}

            manager._save_metadata()

            saved_data = json.loads(metadata_file.read_text())
            assert saved_data["agent1"]["last_updated"] == "2024-01-15"


class TestGetConfiguredAgents:
    """Tests for get_configured_agents method."""

    @pytest.mark.asyncio
    async def test_returns_agents_from_env(self):
        """Should return agents from environment variables."""
        manager = TokenManager()
        manager.metadata = {}

        with patch.dict("os.environ", {
            "AGENT_DATUM_TOKEN": "test-token",
            "AGENT_DATUM_URL": "http://datum:8080"
        }, clear=True):
            agents = await manager.get_configured_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "datum"
        assert agents[0]["url"] == "http://datum:8080"
        assert agents[0]["token_set"] is True

    @pytest.mark.asyncio
    async def test_includes_metadata_when_available(self):
        """Should include metadata for agents."""
        manager = TokenManager()
        manager.metadata = {
            "datum": {
                "last_updated": "2024-01-15",
                "updated_by": "admin@ciris.ai",
                "token_hash": "abc123def456"
            }
        }

        with patch.dict("os.environ", {
            "AGENT_DATUM_TOKEN": "test-token",
            "AGENT_DATUM_URL": "http://datum:8080"
        }, clear=True):
            agents = await manager.get_configured_agents()

        assert agents[0]["last_updated"] == "2024-01-15"
        assert agents[0]["updated_by"] == "admin@ciris.ai"
        assert agents[0]["token_hash"] == "abc123de..."

    @pytest.mark.asyncio
    async def test_returns_empty_list_with_no_agents(self):
        """Should return empty list when no agents configured."""
        manager = TokenManager()
        manager.metadata = {}

        with patch.dict("os.environ", {}, clear=True):
            agents = await manager.get_configured_agents()

        assert agents == []


class TestSetAgentToken:
    """Tests for set_agent_token method."""

    @pytest.mark.asyncio
    async def test_creates_new_env_file(self):
        """Should create .env file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            result = await manager.set_agent_token(
                agent_name="test",
                token="secret-token",
                url="http://test:8080",
                updated_by="admin@ciris.ai"
            )

            assert result is True
            assert env_file.exists()

            content = env_file.read_text()
            assert "AGENT_TEST_TOKEN=secret-token" in content
            assert "AGENT_TEST_URL=http://test:8080" in content

    @pytest.mark.asyncio
    async def test_updates_existing_token(self):
        """Should update existing token in .env file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("AGENT_TEST_TOKEN=old-token\nAGENT_TEST_URL=http://old:8080")

            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            result = await manager.set_agent_token(
                agent_name="test",
                token="new-token",
                url="http://new:8080",
                updated_by="admin@ciris.ai"
            )

            assert result is True

            content = env_file.read_text()
            assert "AGENT_TEST_TOKEN=new-token" in content
            assert "AGENT_TEST_URL=http://new:8080" in content
            assert "old-token" not in content

    @pytest.mark.asyncio
    async def test_updates_metadata(self):
        """Should update metadata with token info."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            await manager.set_agent_token(
                agent_name="test",
                token="secret-token",
                url="http://test:8080",
                updated_by="admin@ciris.ai"
            )

            assert "test" in manager.metadata
            assert manager.metadata["test"]["updated_by"] == "admin@ciris.ai"
            assert manager.metadata["test"]["url"] == "http://test:8080"
            assert "token_hash" in manager.metadata["test"]

    @pytest.mark.asyncio
    async def test_updates_environment_variables(self):
        """Should update current process environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            # Clear any existing env vars
            os.environ.pop("AGENT_TESTENV_TOKEN", None)
            os.environ.pop("AGENT_TESTENV_URL", None)

            await manager.set_agent_token(
                agent_name="testenv",
                token="env-token",
                url="http://testenv:8080",
                updated_by="admin"
            )

            assert os.environ.get("AGENT_TESTENV_TOKEN") == "env-token"
            assert os.environ.get("AGENT_TESTENV_URL") == "http://testenv:8080"

            # Cleanup
            os.environ.pop("AGENT_TESTENV_TOKEN", None)
            os.environ.pop("AGENT_TESTENV_URL", None)

    @pytest.mark.asyncio
    async def test_handles_write_error(self):
        """Should return False on write error."""
        manager = TokenManager()
        manager.env_file_path = Path("/nonexistent/path/.env")
        manager.metadata = {}

        result = await manager.set_agent_token(
            agent_name="test",
            token="token",
            url="http://test:8080",
            updated_by="admin"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_adds_url_after_token_if_missing(self):
        """Should add URL line after token if only token exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("AGENT_TEST_TOKEN=existing-token\nOTHER_VAR=value")

            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            result = await manager.set_agent_token(
                agent_name="test",
                token="existing-token",
                url="http://new:8080",
                updated_by="admin"
            )

            assert result is True

            content = env_file.read_text()
            assert "AGENT_TEST_URL=http://new:8080" in content


class TestRemoveAgentToken:
    """Tests for remove_agent_token method."""

    @pytest.mark.asyncio
    async def test_removes_token_and_url(self):
        """Should remove token and URL from .env file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "# Other config\n"
                "OTHER=value\n"
                "# Test Agent\n"
                "AGENT_TEST_TOKEN=secret\n"
                "AGENT_TEST_URL=http://test:8080\n"
                "MORE=stuff"
            )

            metadata_file = Path(tmpdir) / ".tokens_metadata.json"
            metadata_file.write_text(json.dumps({"test": {"updated_by": "admin"}}))

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {"test": {"updated_by": "admin"}}

            result = await manager.remove_agent_token("test")

            assert result is True

            content = env_file.read_text()
            assert "AGENT_TEST_TOKEN" not in content
            assert "AGENT_TEST_URL" not in content
            assert "OTHER=value" in content
            assert "MORE=stuff" in content

    @pytest.mark.asyncio
    async def test_removes_from_metadata(self):
        """Should remove agent from metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("AGENT_TEST_TOKEN=secret\nAGENT_TEST_URL=http://test:8080")

            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {"test": {"updated_by": "admin"}}

            await manager.remove_agent_token("test")

            assert "test" not in manager.metadata

    @pytest.mark.asyncio
    async def test_removes_from_environment(self):
        """Should remove from current process environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("AGENT_TESTREM_TOKEN=secret")

            metadata_file = Path(tmpdir) / ".tokens_metadata.json"

            manager = TokenManager()
            manager.env_file_path = env_file
            manager.tokens_metadata_file = metadata_file
            manager.metadata = {}

            # Set env vars
            os.environ["AGENT_TESTREM_TOKEN"] = "secret"
            os.environ["AGENT_TESTREM_URL"] = "http://test:8080"

            await manager.remove_agent_token("testrem")

            assert "AGENT_TESTREM_TOKEN" not in os.environ
            assert "AGENT_TESTREM_URL" not in os.environ

    @pytest.mark.asyncio
    async def test_returns_false_if_file_not_exists(self):
        """Should return False if .env file doesn't exist."""
        manager = TokenManager()
        manager.env_file_path = Path("/nonexistent/.env")
        manager.metadata = {}

        result = await manager.remove_agent_token("test")

        assert result is False

    @pytest.mark.asyncio
    async def test_handles_removal_error(self):
        """Should return False on removal error."""
        manager = TokenManager()
        manager.env_file_path = Path("/nonexistent/.env")
        manager.metadata = {}

        result = await manager.remove_agent_token("test")

        assert result is False


class TestValidateToken:
    """Tests for validate_token method."""

    @pytest.mark.asyncio
    async def test_returns_true_for_matching_hash(self):
        """Should return True when token hash matches."""
        token = "secret-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        manager = TokenManager()
        manager.metadata = {
            "test": {"token_hash": token_hash}
        }

        result = await manager.validate_token("test", token)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_for_non_matching_hash(self):
        """Should return False when token hash doesn't match."""
        manager = TokenManager()
        manager.metadata = {
            "test": {"token_hash": "different_hash"}
        }

        result = await manager.validate_token("test", "wrong-token")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_agent(self):
        """Should return False for unknown agent."""
        manager = TokenManager()
        manager.metadata = {}

        result = await manager.validate_token("unknown", "any-token")

        assert result is False

    @pytest.mark.asyncio
    async def test_case_insensitive_agent_name(self):
        """Should handle case-insensitive agent names."""
        token = "secret-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        manager = TokenManager()
        manager.metadata = {
            "test": {"token_hash": token_hash}
        }

        result = await manager.validate_token("TEST", token)

        assert result is True
