"""
Unit tests for TokenManager class.

Tests secure token management, validation, and file operations.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest


class TestTokenManagerInit:
    """Test TokenManager initialization."""

    def test_init_creates_instance(self):
        """Test TokenManager can be instantiated."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            manager = TokenManager(env_file_path=env_path)

            assert manager.env_file_path == Path(env_path)
            assert manager.metadata == {}

    def test_init_loads_existing_metadata(self):
        """Test TokenManager loads existing metadata file."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create metadata file
            metadata_path = Path(tmpdir) / ".tokens_metadata.json"
            metadata_content = {
                "test_agent": {
                    "last_updated": "2024-01-01T00:00:00Z",
                    "updated_by": "admin@ciris.ai",
                    "token_hash": "abc123",
                }
            }
            metadata_path.write_text(json.dumps(metadata_content))

            # Change working directory to tmpdir temporarily
            original_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                manager = TokenManager(env_file_path=".env")
                assert "test_agent" in manager.metadata
                assert manager.metadata["test_agent"]["updated_by"] == "admin@ciris.ai"
            finally:
                os.chdir(original_cwd)


class TestGetConfiguredAgents:
    """Test get_configured_agents method."""

    @pytest.mark.asyncio
    async def test_get_configured_agents_empty(self):
        """Test get_configured_agents returns empty list when no agents configured."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            # Clear any existing AGENT_ env vars for this test
            agent_vars = [k for k in os.environ if k.startswith("AGENT_") and k.endswith("_TOKEN")]
            original_values = {k: os.environ.pop(k) for k in agent_vars}

            try:
                agents = await manager.get_configured_agents()
                assert isinstance(agents, list)
            finally:
                # Restore original values
                os.environ.update(original_values)

    @pytest.mark.asyncio
    async def test_get_configured_agents_finds_agents(self):
        """Test get_configured_agents finds agents from environment."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            # Set up test environment variables
            os.environ["AGENT_TESTBOT_TOKEN"] = "test-token"
            os.environ["AGENT_TESTBOT_URL"] = "http://localhost:8080"

            try:
                agents = await manager.get_configured_agents()

                agent_names = [a["name"] for a in agents]
                assert "testbot" in agent_names

                testbot = next(a for a in agents if a["name"] == "testbot")
                assert testbot["url"] == "http://localhost:8080"
                assert testbot["token_set"] is True
            finally:
                del os.environ["AGENT_TESTBOT_TOKEN"]
                del os.environ["AGENT_TESTBOT_URL"]

    @pytest.mark.asyncio
    async def test_get_configured_agents_includes_metadata(self):
        """Test get_configured_agents includes metadata when available."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))
            manager.metadata = {
                "myagent": {
                    "last_updated": "2024-01-01T00:00:00Z",
                    "updated_by": "test@ciris.ai",
                    "token_hash": "abcdef123456789012345678901234567890",
                }
            }

            os.environ["AGENT_MYAGENT_TOKEN"] = "secret"
            os.environ["AGENT_MYAGENT_URL"] = "http://test"

            try:
                agents = await manager.get_configured_agents()

                myagent = next(a for a in agents if a["name"] == "myagent")
                assert myagent["last_updated"] == "2024-01-01T00:00:00Z"
                assert myagent["updated_by"] == "test@ciris.ai"
                # Token hash should be truncated
                assert myagent["token_hash"] == "abcdef12..."
            finally:
                del os.environ["AGENT_MYAGENT_TOKEN"]
                del os.environ["AGENT_MYAGENT_URL"]


class TestSetAgentToken:
    """Test set_agent_token method."""

    @pytest.mark.asyncio
    async def test_set_agent_token_creates_new_env_file(self):
        """Test set_agent_token creates .env file if it doesn't exist."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            metadata_path = Path(tmpdir) / ".tokens_metadata.json"

            # Change working directory for metadata file
            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=env_path)

                result = await manager.set_agent_token(
                    agent_name="newagent",
                    token="test-token-123",
                    url="http://localhost:8080",
                    updated_by="test@ciris.ai"
                )

                assert result is True
                assert Path(env_path).exists()

                # Verify content
                content = Path(env_path).read_text()
                assert "AGENT_NEWAGENT_TOKEN=test-token-123" in content
                assert "AGENT_NEWAGENT_URL=http://localhost:8080" in content

                # Verify metadata was saved
                assert metadata_path.exists()
                metadata = json.loads(metadata_path.read_text())
                assert "newagent" in metadata
                assert metadata["newagent"]["updated_by"] == "test@ciris.ai"

                # Verify env vars were set
                assert os.environ.get("AGENT_NEWAGENT_TOKEN") == "test-token-123"
            finally:
                os.chdir(original_cwd)
                # Cleanup env vars
                if "AGENT_NEWAGENT_TOKEN" in os.environ:
                    del os.environ["AGENT_NEWAGENT_TOKEN"]
                if "AGENT_NEWAGENT_URL" in os.environ:
                    del os.environ["AGENT_NEWAGENT_URL"]

    @pytest.mark.asyncio
    async def test_set_agent_token_updates_existing(self):
        """Test set_agent_token updates existing token."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")

            # Create existing .env
            Path(env_path).write_text("AGENT_EXISTING_TOKEN=old-token\nAGENT_EXISTING_URL=http://old")

            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=env_path)

                result = await manager.set_agent_token(
                    agent_name="existing",
                    token="new-token",
                    url="http://new",
                    updated_by="admin@ciris.ai"
                )

                assert result is True

                content = Path(env_path).read_text()
                assert "AGENT_EXISTING_TOKEN=new-token" in content
                assert "AGENT_EXISTING_URL=http://new" in content
                assert "old-token" not in content
            finally:
                os.chdir(original_cwd)
                if "AGENT_EXISTING_TOKEN" in os.environ:
                    del os.environ["AGENT_EXISTING_TOKEN"]
                if "AGENT_EXISTING_URL" in os.environ:
                    del os.environ["AGENT_EXISTING_URL"]

    @pytest.mark.asyncio
    async def test_set_agent_token_stores_hash(self):
        """Test set_agent_token stores token hash in metadata."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")

            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=env_path)

                token = "my-secret-token"
                expected_hash = hashlib.sha256(token.encode()).hexdigest()

                await manager.set_agent_token(
                    agent_name="hashtest",
                    token=token,
                    url="http://test",
                    updated_by="test@ciris.ai"
                )

                assert manager.metadata["hashtest"]["token_hash"] == expected_hash
            finally:
                os.chdir(original_cwd)
                if "AGENT_HASHTEST_TOKEN" in os.environ:
                    del os.environ["AGENT_HASHTEST_TOKEN"]
                if "AGENT_HASHTEST_URL" in os.environ:
                    del os.environ["AGENT_HASHTEST_URL"]

    @pytest.mark.asyncio
    async def test_set_agent_token_normalizes_name(self):
        """Test set_agent_token normalizes agent name to lowercase."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")

            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=env_path)

                await manager.set_agent_token(
                    agent_name="MyAgent",
                    token="token",
                    url="http://test",
                    updated_by="test@ciris.ai"
                )

                # Metadata key should be lowercase
                assert "myagent" in manager.metadata

                # But env var should be uppercase
                content = Path(env_path).read_text()
                assert "AGENT_MYAGENT_TOKEN=" in content
            finally:
                os.chdir(original_cwd)
                if "AGENT_MYAGENT_TOKEN" in os.environ:
                    del os.environ["AGENT_MYAGENT_TOKEN"]
                if "AGENT_MYAGENT_URL" in os.environ:
                    del os.environ["AGENT_MYAGENT_URL"]


class TestRemoveAgentToken:
    """Test remove_agent_token method."""

    @pytest.mark.asyncio
    async def test_remove_agent_token_returns_false_if_no_env(self):
        """Test remove_agent_token returns False if .env doesn't exist."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            result = await manager.remove_agent_token("nonexistent")
            assert result is False

    @pytest.mark.asyncio
    async def test_remove_agent_token_removes_lines(self):
        """Test remove_agent_token removes token and URL lines."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")

            # Create .env with token
            Path(env_path).write_text(
                "OTHER_VAR=keep\n"
                "# Toremove Agent\n"
                "AGENT_TOREMOVE_TOKEN=secret\n"
                "AGENT_TOREMOVE_URL=http://test\n"
                "ANOTHER_VAR=also_keep"
            )

            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=env_path)
                manager.metadata["toremove"] = {"token_hash": "abc"}

                # Set env vars
                os.environ["AGENT_TOREMOVE_TOKEN"] = "secret"
                os.environ["AGENT_TOREMOVE_URL"] = "http://test"

                result = await manager.remove_agent_token("toremove")

                assert result is True

                content = Path(env_path).read_text()
                assert "AGENT_TOREMOVE_TOKEN" not in content
                assert "AGENT_TOREMOVE_URL" not in content
                assert "OTHER_VAR=keep" in content
                assert "ANOTHER_VAR=also_keep" in content

                # Check metadata was removed
                assert "toremove" not in manager.metadata

                # Check env vars were removed
                assert "AGENT_TOREMOVE_TOKEN" not in os.environ
                assert "AGENT_TOREMOVE_URL" not in os.environ
            finally:
                os.chdir(original_cwd)


class TestValidateToken:
    """Test validate_token method."""

    @pytest.mark.asyncio
    async def test_validate_token_returns_false_for_unknown_agent(self):
        """Test validate_token returns False for unknown agent."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            result = await manager.validate_token("unknown", "any-token")
            assert result is False

    @pytest.mark.asyncio
    async def test_validate_token_returns_true_for_correct_token(self):
        """Test validate_token returns True when hash matches."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            token = "correct-token"
            token_hash = hashlib.sha256(token.encode()).hexdigest()

            manager.metadata["validagent"] = {
                "token_hash": token_hash,
            }

            result = await manager.validate_token("validagent", token)
            assert result is True

    @pytest.mark.asyncio
    async def test_validate_token_returns_false_for_wrong_token(self):
        """Test validate_token returns False when hash doesn't match."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            correct_token = "correct-token"
            wrong_token = "wrong-token"
            token_hash = hashlib.sha256(correct_token.encode()).hexdigest()

            manager.metadata["wrongtest"] = {
                "token_hash": token_hash,
            }

            result = await manager.validate_token("wrongtest", wrong_token)
            assert result is False

    @pytest.mark.asyncio
    async def test_validate_token_normalizes_agent_name(self):
        """Test validate_token normalizes agent name to lowercase."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TokenManager(env_file_path=os.path.join(tmpdir, ".env"))

            token = "test-token"
            token_hash = hashlib.sha256(token.encode()).hexdigest()

            # Store with lowercase
            manager.metadata["normalizetest"] = {
                "token_hash": token_hash,
            }

            # Validate with uppercase should still work
            result = await manager.validate_token("NormalizeTest", token)
            assert result is True


class TestSaveMetadata:
    """Test _save_metadata method."""

    def test_save_metadata_writes_json(self):
        """Test _save_metadata writes valid JSON."""
        from api.token_manager import TokenManager

        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                manager = TokenManager(env_file_path=".env")
                manager.metadata = {
                    "agent1": {"last_updated": "2024-01-01", "token_hash": "abc"},
                    "agent2": {"last_updated": "2024-01-02", "token_hash": "def"},
                }

                manager._save_metadata()

                metadata_path = Path(".tokens_metadata.json")
                assert metadata_path.exists()

                saved = json.loads(metadata_path.read_text())
                assert saved == manager.metadata
            finally:
                os.chdir(original_cwd)
