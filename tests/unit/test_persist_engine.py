"""
Tests for the persist_engine singleton wrapper.

Covers the v0.1.4 worker-race mitigations:
- error propagation to /health (the dominant operational gap from the
  Phase 1 deploy: failed workers reported `init_error: null`)
- environment-driven gate (CIRISLENS_PERSIST_DISABLED, missing DSN,
  missing wheel)

The full advisory-lock serialization across concurrent workers can
only be exercised with a real Postgres + multiple processes. Those
live in the integration suite when the persist v0.1.5 fix lands; the
unit suite here pins the per-call behavior in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts from a clean module state."""
    import persist_engine

    persist_engine._State.engine = None
    persist_engine._State.disabled = False
    persist_engine._State.init_error = None
    yield


class TestStatusShape:
    """The /health probe consumes status() — shape must stay stable."""

    def test_status_keys(self):
        from persist_engine import status

        s = status()
        # v0.2.2 added steward_ready alongside scrubber_ready when the
        # federation steward identity became a separate concern from
        # the scrub identity. v0.3.1 added steward_pqc_ready when the
        # ML-DSA-65 cold-path identity became its own concern (persist
        # auto-fires the cold path when configured). /health consumes
        # all three flags.
        assert set(s.keys()) == {
            "initialized", "disabled", "init_error",
            "scrubber_ready", "steward_ready", "steward_pqc_ready",
        }
        assert s["initialized"] is False
        assert s["disabled"] is False
        assert s["init_error"] is None
        assert s["scrubber_ready"] is False
        assert s["steward_ready"] is False
        assert s["steward_pqc_ready"] is False


class TestEnvironmentGates:
    """Three reasons initialize() returns None without raising."""

    @pytest.mark.asyncio
    async def test_disabled_env_var(self, monkeypatch):
        from persist_engine import initialize, status

        monkeypatch.setenv("CIRISLENS_PERSIST_DISABLED", "true")
        result = await initialize()
        assert result is None
        s = status()
        assert s["disabled"] is True
        assert s["initialized"] is False

    @pytest.mark.asyncio
    async def test_missing_dsn(self, monkeypatch):
        from persist_engine import initialize, status

        monkeypatch.delenv("CIRISLENS_PERSIST_DISABLED", raising=False)
        monkeypatch.delenv("CIRISLENS_DB_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = await initialize()
        assert result is None
        s = status()
        assert s["initialized"] is False
        assert "DATABASE_URL" in s["init_error"]

    @pytest.mark.asyncio
    async def test_dsn_falls_back_to_database_url(self, monkeypatch):
        """CIRISLENS_DB_URL preferred; DATABASE_URL is the fallback."""
        import persist_engine

        monkeypatch.delenv("CIRISLENS_PERSIST_DISABLED", raising=False)
        monkeypatch.delenv("CIRISLENS_DB_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        # Patch asyncpg.connect to fail loudly (we just want to confirm
        # we got past the DSN check).
        async def fake_connect(_dsn):
            raise ConnectionRefusedError("planted error")

        monkeypatch.setattr(persist_engine.asyncpg, "connect", fake_connect)

        # Wheel available? If not, the test still confirms the DSN path
        # is chosen — initialize returns None at the wheel-import gate
        # rather than the DSN gate.
        with pytest.raises(ConnectionRefusedError):
            await persist_engine.initialize()


class TestInitErrorPropagation:
    """The Phase 1 deploy lesson: when cp.Engine() raises, init_error
    must surface via status() so /health probes don't lie."""

    @pytest.mark.asyncio
    async def test_engine_failure_captured_in_status(self, monkeypatch):
        """A RuntimeError from cp.Engine() — the v0.1.4 migration race
        symptom — must populate _State.init_error and return None
        rather than propagate."""
        import persist_engine

        monkeypatch.setenv("CIRISLENS_DB_URL", "postgres://fake")
        monkeypatch.delenv("CIRISLENS_PERSIST_DISABLED", raising=False)

        # Mock asyncpg lock connection to no-op cleanly
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.close = AsyncMock()

        async def fake_connect(_dsn):
            return mock_conn

        monkeypatch.setattr(persist_engine.asyncpg, "connect", fake_connect)

        # Mock cp.Engine to raise the v0.1.4 race error
        fake_cp = MagicMock()
        fake_cp.__version__ = "0.1.4-test"
        fake_cp.SUPPORTED_SCHEMA_VERSIONS = ["2.7.0"]
        fake_cp.Engine.side_effect = RuntimeError(
            "migrations: backend: migrations: `error asserting migrations table`, `db error`"
        )

        with patch.dict(sys.modules, {"ciris_persist": fake_cp}):
            result = await persist_engine.initialize()

        assert result is None, "engine ctor failure should return None, not propagate"
        s = persist_engine.status()
        assert s["initialized"] is False
        assert s["init_error"] is not None
        assert "RuntimeError" in s["init_error"]
        assert "asserting migrations table" in s["init_error"]

    @pytest.mark.asyncio
    async def test_lock_acquired_then_released(self, monkeypatch):
        """Advisory lock should be acquired before cp.Engine() and
        released after, regardless of success or failure."""
        import persist_engine

        monkeypatch.setenv("CIRISLENS_DB_URL", "postgres://fake")
        monkeypatch.delenv("CIRISLENS_PERSIST_DISABLED", raising=False)

        # Track call sequence on the lock connection
        call_log = []
        mock_conn = MagicMock()

        async def execute(sql, *args):
            call_log.append((sql, args))

        async def close():
            call_log.append(("close",))

        mock_conn.execute = execute
        mock_conn.close = close

        async def fake_connect(_dsn):
            call_log.append(("connect",))
            return mock_conn

        monkeypatch.setattr(persist_engine.asyncpg, "connect", fake_connect)

        fake_cp = MagicMock()
        fake_cp.__version__ = "0.1.4-test"
        fake_cp.SUPPORTED_SCHEMA_VERSIONS = ["2.7.0"]
        fake_engine = MagicMock()
        fake_engine.public_key_b64.return_value = "FAKEPUBKEYBASE64"
        fake_cp.Engine.return_value = fake_engine

        with patch.dict(sys.modules, {"ciris_persist": fake_cp}):
            await persist_engine.initialize()

        # Sequence: connect → advisory_lock → (engine ctor) → advisory_unlock → close
        sqls = [c[0] for c in call_log if c[0] not in ("connect", "close")]
        markers = [c[0] for c in call_log if c[0] in ("connect", "close")]
        assert markers == ["connect", "close"]
        assert any("pg_advisory_lock" in s for s in sqls)
        assert any("pg_advisory_unlock" in s for s in sqls)
        # Lock comes before unlock
        lock_idx = next(i for i, s in enumerate(sqls) if "pg_advisory_lock" in s)
        unlock_idx = next(i for i, s in enumerate(sqls) if "pg_advisory_unlock" in s)
        assert lock_idx < unlock_idx

    @pytest.mark.asyncio
    async def test_lock_released_even_on_engine_failure(self, monkeypatch):
        """If cp.Engine() raises, the unlock + close still fire."""
        import persist_engine

        monkeypatch.setenv("CIRISLENS_DB_URL", "postgres://fake")
        monkeypatch.delenv("CIRISLENS_PERSIST_DISABLED", raising=False)

        unlock_called = False
        close_called = False
        mock_conn = MagicMock()

        async def execute(sql, *_args):
            nonlocal unlock_called
            if "pg_advisory_unlock" in sql:
                unlock_called = True

        async def close():
            nonlocal close_called
            close_called = True

        mock_conn.execute = execute
        mock_conn.close = close

        async def fake_connect(_dsn):
            return mock_conn

        monkeypatch.setattr(persist_engine.asyncpg, "connect", fake_connect)

        fake_cp = MagicMock()
        fake_cp.__version__ = "0.1.4-test"
        fake_cp.SUPPORTED_SCHEMA_VERSIONS = ["2.7.0"]
        fake_cp.Engine.side_effect = RuntimeError("simulated engine failure")

        with patch.dict(sys.modules, {"ciris_persist": fake_cp}):
            await persist_engine.initialize()

        assert unlock_called, "advisory_unlock must fire on engine failure too"
        assert close_called, "lock_conn.close must fire on engine failure too"


class TestIdempotency:
    """Calling initialize() twice should be a no-op on the second
    call (multi-startup-hook safety)."""

    @pytest.mark.asyncio
    async def test_second_call_returns_existing(self, monkeypatch):
        from persist_engine import _State, initialize

        sentinel = MagicMock()
        _State.engine = sentinel

        result = await initialize()
        assert result is sentinel
