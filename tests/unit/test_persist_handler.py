"""
Tests for the Phase 2a feature-flagged delegation in
`accord_api.py POST /api/v1/accord/events`.

Covers the routing layer that decides whether to send a batch through
the ciris-persist Engine or fall back to the legacy code path:

- Feature flag (CIRISLENS_USE_PERSIST_ENGINE) gates the behaviour
- Engine None / scrubber-not-ready forces fallback
- Connectivity-event batches always stay on legacy path
- Mock-LLM trace batches always stay on legacy path
- Engine error → HTTP code mapping per CIRISPersist INTEGRATION_LENS.md §4
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))


def _trace_event(event_type: str = "THOUGHT_START", data: dict | None = None) -> dict:
    """Build a minimal AccordTraceEvent dict for AccordEventsRequest
    construction. Kept loose — the routing helpers only inspect a
    handful of fields."""
    return {
        "trace": {
            "trace_id": "trace-test-001",
            "thought_id": "th_test_001",
            "task_id": "task-test",
            "agent_id_hash": "abcd1234",
            "started_at": "2026-05-01T00:00:00+00:00",
            "completed_at": "2026-05-01T00:00:01+00:00",
            "components": [
                {
                    "component_type": "thought",
                    "event_type": event_type,
                    "timestamp": "2026-05-01T00:00:00+00:00",
                    "data": data or {},
                },
            ],
            "signature": "x" * 86,
            "signature_key_id": "agent-test",
        },
    }


def _build_request(events: list[dict], trace_level: str = "generic"):
    """Materialize an AccordEventsRequest pydantic instance."""
    from accord_api import AccordEventsRequest

    return AccordEventsRequest.model_validate({
        "events": events,
        "batch_timestamp": "2026-05-01T00:00:00+00:00",
        "consent_timestamp": "2026-05-01T00:00:00+00:00",
        "trace_level": trace_level,
    })


# ─── _is_connectivity_batch ────────────────────────────────────────


class TestIsConnectivityBatch:
    def test_startup_only_batch_is_connectivity(self):
        from accord_api import _is_connectivity_batch

        req = _build_request([_trace_event(event_type="startup")])
        assert _is_connectivity_batch(req) is True

    def test_shutdown_only_batch_is_connectivity(self):
        from accord_api import _is_connectivity_batch

        req = _build_request([_trace_event(event_type="shutdown")])
        assert _is_connectivity_batch(req) is True

    def test_mixed_batch_is_not_connectivity(self):
        from accord_api import _is_connectivity_batch

        req = _build_request([
            _trace_event(event_type="startup"),
            _trace_event(event_type="THOUGHT_START"),
        ])
        assert _is_connectivity_batch(req) is False

    def test_reasoning_batch_is_not_connectivity(self):
        from accord_api import _is_connectivity_batch

        req = _build_request([_trace_event(event_type="THOUGHT_START")])
        assert _is_connectivity_batch(req) is False

    def test_empty_batch_is_not_connectivity(self):
        from accord_api import _is_connectivity_batch

        # AccordEventsRequest allows empty events list at the schema
        # level; we still want connectivity detection to be False so
        # the empty batch isn't routed to the connectivity-event table.
        try:
            req = _build_request([])
        except Exception:
            pytest.skip("empty batch rejected at validation; covered by schema not handler")
        assert _is_connectivity_batch(req) is False


# ─── _has_mock_llm_traces ──────────────────────────────────────────


class TestHasMockLlmTraces:
    def test_mock_in_models_used_detected(self):
        from accord_api import _has_mock_llm_traces

        req = _build_request([
            _trace_event(data={"models_used": ["llama4scout (mock)"]}),
        ], trace_level="detailed")
        assert _has_mock_llm_traces(req) is True

    def test_real_models_not_detected(self):
        from accord_api import _has_mock_llm_traces

        req = _build_request([
            _trace_event(data={"models_used": ["meta-llama/Llama-4-Maverick-17B"]}),
        ], trace_level="detailed")
        assert _has_mock_llm_traces(req) is False

    def test_generic_no_models_used_returns_false(self):
        """Generic traces don't include models_used — the sniff has
        no signal so returns False (best-effort; documented limit)."""
        from accord_api import _has_mock_llm_traces

        req = _build_request([_trace_event(data={})])
        assert _has_mock_llm_traces(req) is False

    def test_mock_case_insensitive(self):
        from accord_api import _has_mock_llm_traces

        req = _build_request([
            _trace_event(data={"models_used": ["FAKEMOCKMODEL"]}),
        ], trace_level="detailed")
        assert _has_mock_llm_traces(req) is True


# ─── _persist_engine_active ────────────────────────────────────────


class TestPersistEngineActive:
    def test_flag_off_returns_false(self, monkeypatch):
        from accord_api import _persist_engine_active

        monkeypatch.delenv("CIRISLENS_USE_PERSIST_ENGINE", raising=False)
        assert _persist_engine_active("generic") is False

    @pytest.mark.parametrize("flag_val", ["true", "TRUE", "1", "yes", "on"])
    def test_flag_on_with_engine_returns_true(self, monkeypatch, flag_val):
        import persist_engine
        from accord_api import _persist_engine_active

        monkeypatch.setenv("CIRISLENS_USE_PERSIST_ENGINE", flag_val)
        with patch.object(persist_engine, "get_engine", return_value=MagicMock()):
            assert _persist_engine_active("generic") is True

    def test_engine_none_returns_false(self, monkeypatch):
        import persist_engine
        from accord_api import _persist_engine_active

        monkeypatch.setenv("CIRISLENS_USE_PERSIST_ENGINE", "true")
        with patch.object(persist_engine, "get_engine", return_value=None):
            assert _persist_engine_active("generic") is False

    def test_non_generic_without_scrubber_returns_false(self, monkeypatch):
        """Critical safety check: if the scrubber isn't wired, persist
        would NullScrubber a detailed/full_traces request and PII would
        land unscrubbed. The handler MUST refuse and fall back to legacy."""
        import persist_engine
        from accord_api import _persist_engine_active

        monkeypatch.setenv("CIRISLENS_USE_PERSIST_ENGINE", "true")
        with patch.object(persist_engine, "get_engine", return_value=MagicMock()), \
             patch.object(persist_engine, "scrubber_ready", return_value=False):
            assert _persist_engine_active("detailed") is False
            assert _persist_engine_active("full_traces") is False

    def test_generic_without_scrubber_still_active(self, monkeypatch):
        """Generic traces are content-free by design — persist bypasses
        the scrubber callback entirely, so scrubber_ready=False is fine."""
        import persist_engine
        from accord_api import _persist_engine_active

        monkeypatch.setenv("CIRISLENS_USE_PERSIST_ENGINE", "true")
        with patch.object(persist_engine, "get_engine", return_value=MagicMock()), \
             patch.object(persist_engine, "scrubber_ready", return_value=False):
            assert _persist_engine_active("generic") is True


# ─── _delegate_to_persist (error mapping) ──────────────────────────


class TestDelegateToPersistErrorMapping:
    @pytest.mark.asyncio
    async def test_unknown_key_maps_to_401(self):
        from fastapi import HTTPException

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.side_effect = ValueError("verify: Unknown key: agent-mystery")

        with patch.object(persist_engine, "get_engine", return_value=engine), \
             pytest.raises(HTTPException) as exc:
            await _delegate_to_persist(b"{}")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_signature_mismatch_maps_to_422(self):
        from fastapi import HTTPException

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.side_effect = ValueError("verify: signature mismatch")

        with patch.object(persist_engine, "get_engine", return_value=engine), \
             pytest.raises(HTTPException) as exc:
            await _delegate_to_persist(b"{}")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_schema_error_maps_to_422(self):
        from fastapi import HTTPException

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.side_effect = ValueError("schema: missing trace_id")

        with patch.object(persist_engine, "get_engine", return_value=engine), \
             pytest.raises(HTTPException) as exc:
            await _delegate_to_persist(b"{}")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_runtime_error_maps_to_503_with_retry_after(self):
        from fastapi import HTTPException

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.side_effect = RuntimeError("store: connection refused")

        with patch.object(persist_engine, "get_engine", return_value=engine), \
             pytest.raises(HTTPException) as exc:
            await _delegate_to_persist(b"{}")
        assert exc.value.status_code == 503
        assert exc.value.headers.get("Retry-After") == "5"

    @pytest.mark.asyncio
    async def test_success_returns_summary(self):
        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        summary = {
            "envelopes_processed": 1,
            "trace_events_inserted": 12,
            "trace_events_conflicted": 0,
            "trace_llm_calls_inserted": 5,
            "scrubbed_fields": 3,
            "signatures_verified": 1,
        }
        engine.receive_and_persist.return_value = summary

        with patch.object(persist_engine, "get_engine", return_value=engine):
            result = await _delegate_to_persist(b'{"events":[]}')
        assert result == summary
        engine.receive_and_persist.assert_called_once_with(b'{"events":[]}')
