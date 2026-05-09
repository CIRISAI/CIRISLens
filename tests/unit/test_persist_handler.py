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
    async def test_success_adapts_to_accord_events_response_shape(self):
        """Persist's BatchSummary dict is translated to the legacy
        AccordEventsResponse shape the route's response_model expects.
        Without this adapter, FastAPI's pydantic response validation
        threw 500 on every successful delegation — bridge observed
        27× DELEGATE_RESULT paired with 27× HTTP 500, with the agent
        retrying and hitting the dedup index on every replay."""
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

        # AccordEventsResponse-compatible shape (status/received/accepted/rejected)
        assert result == {
            "status": "ok",
            "received": 1,
            "accepted": 1,
            "rejected": 0,
            "rejected_traces": None,
            "errors": None,
        }
        engine.receive_and_persist.assert_called_once_with(b'{"events":[]}')

    @pytest.mark.asyncio
    async def test_dedup_replay_is_still_accepted(self):
        """Replay envelopes (events_inserted=0, events_conflicted=N) are
        non-error from the agent's perspective — verify + scrub +
        idempotent-ON-CONFLICT all worked. Must NOT return rejected>0."""
        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        replay_summary = {
            "envelopes_processed": 1,
            "trace_events_inserted": 0,
            "trace_events_conflicted": 19,
            "trace_llm_calls_inserted": 0,
            "scrubbed_fields": 0,
            "signatures_verified": 1,
        }
        engine.receive_and_persist.return_value = replay_summary

        with patch.object(persist_engine, "get_engine", return_value=engine):
            result = await _delegate_to_persist(b'{"events":[]}')

        # Replay is accepted (idempotent), not rejected.
        assert result["status"] == "ok"
        assert result["received"] == 1
        assert result["accepted"] == 1
        assert result["rejected"] == 0


# ─── _rewrite_legacy_schema_stamp (CIRISLens#9) ────────────────────


class TestLegacySchemaStampRewrite:
    """Pre-2.7.8.9 agents stamp `trace_schema_version: "2.7.0"` but
    sign the 2-field legacy canonical. Persist 0.4.4 dispatches by
    stamp, so "2.7.0" → 9-field canonicalizer → strict-verify fails.
    Lens rewrites the stamp to "2.7.legacy" before delegation so
    persist routes to canonical_payload_value_legacy."""

    def test_top_level_2_7_0_rewritten_to_legacy(self):
        import json as _json

        from accord_api import _rewrite_legacy_schema_stamp

        body = _json.dumps({
            "trace_schema_version": "2.7.0",
            "trace_level": "detailed",
            "events": [],
        }).encode("utf-8")

        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 1
        obj = _json.loads(out)
        assert obj["trace_schema_version"] == "2.7.legacy"

    def test_per_trace_2_7_0_rewritten_to_legacy(self):
        import json as _json

        from accord_api import _rewrite_legacy_schema_stamp

        body = _json.dumps({
            "trace_schema_version": "2.7.0",
            "events": [
                {
                    "event_type": "complete_trace",
                    "trace_level": "detailed",
                    "trace": {
                        "trace_schema_version": "2.7.0",
                        "components": [],
                        "trace_level": "detailed",
                    },
                },
            ],
        }).encode("utf-8")

        out, count = _rewrite_legacy_schema_stamp(body)

        # One envelope-level + one per-trace
        assert count == 2
        obj = _json.loads(out)
        assert obj["trace_schema_version"] == "2.7.legacy"
        assert obj["events"][0]["trace"]["trace_schema_version"] == "2.7.legacy"

    def test_2_7_9_left_alone(self):
        """Modern emitters (post-CIRISAgent#710 / commit 431b0e0ae)
        stamp "2.7.9" and sign the 9-field canonical; the rewrite
        MUST NOT touch them."""
        import json as _json

        from accord_api import _rewrite_legacy_schema_stamp

        body = _json.dumps({
            "trace_schema_version": "2.7.9",
            "events": [
                {
                    "trace": {"trace_schema_version": "2.7.9"},
                },
            ],
        }).encode("utf-8")

        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 0
        # No-op should return the original bytes unchanged.
        assert out is body

    def test_already_legacy_left_alone(self):
        """Persist's own serde-default stamps absent fields as
        "2.7.legacy"; an envelope already at "2.7.legacy" should be
        a no-op."""
        import json as _json

        from accord_api import _rewrite_legacy_schema_stamp

        body = _json.dumps({"trace_schema_version": "2.7.legacy", "events": []}).encode("utf-8")

        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 0
        assert out is body

    def test_invalid_json_returned_unchanged(self):
        """Malformed bytes pass through untouched so persist's typed
        parser surfaces the structured schema error rather than the
        rewrite hiding it behind a JSON exception here."""
        from accord_api import _rewrite_legacy_schema_stamp

        body = b"{not valid json"
        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 0
        assert out is body

    def test_non_dict_root_returned_unchanged(self):
        """JSON arrays / scalars at the root aren't BatchEnvelopes;
        leave them for persist to reject."""
        from accord_api import _rewrite_legacy_schema_stamp

        body = b"[]"
        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 0
        assert out is body

    def test_mixed_versions_only_rewrites_2_7_0_traces(self):
        """A batch could carry traces at different stamps if the agent
        is mid-flight during a config change. Only the "2.7.0" traces
        should flip."""
        import json as _json

        from accord_api import _rewrite_legacy_schema_stamp

        body = _json.dumps({
            "events": [
                {"trace": {"trace_schema_version": "2.7.0"}},
                {"trace": {"trace_schema_version": "2.7.9"}},
            ],
        }).encode("utf-8")

        out, count = _rewrite_legacy_schema_stamp(body)

        assert count == 1
        obj = _json.loads(out)
        assert obj["events"][0]["trace"]["trace_schema_version"] == "2.7.legacy"
        assert obj["events"][1]["trace"]["trace_schema_version"] == "2.7.9"

    @pytest.mark.asyncio
    async def test_delegate_passes_rewritten_body_to_engine(self):
        """End-to-end: a 2.7.0-stamped envelope reaches `_delegate_to_persist`,
        and `engine.receive_and_persist` is called with the legacy-stamped
        body (not the inbound bytes)."""
        import json as _json

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.return_value = {
            "envelopes_processed": 1,
            "trace_events_inserted": 1,
            "trace_events_conflicted": 0,
            "trace_llm_calls_inserted": 0,
            "scrubbed_fields": 0,
            "signatures_verified": 1,
        }

        inbound = _json.dumps({
            "trace_schema_version": "2.7.0",
            "events": [],
        }).encode("utf-8")

        with patch.object(persist_engine, "get_engine", return_value=engine):
            await _delegate_to_persist(inbound)

        engine.receive_and_persist.assert_called_once()
        delegated = engine.receive_and_persist.call_args.args[0]
        assert delegated != inbound
        delegated_obj = _json.loads(delegated)
        assert delegated_obj["trace_schema_version"] == "2.7.legacy"

    @pytest.mark.asyncio
    async def test_delegate_passes_through_when_no_rewrite_needed(self):
        """Modern emitters' bytes reach the engine byte-identical so
        persist's `wire_body_sha256` log lines stay equal to lens's
        body_sha (CIRISPersist#6 correlation)."""
        import json as _json

        import persist_engine
        from accord_api import _delegate_to_persist

        engine = MagicMock()
        engine.receive_and_persist.return_value = {
            "envelopes_processed": 1,
            "trace_events_inserted": 1,
            "trace_events_conflicted": 0,
            "trace_llm_calls_inserted": 0,
            "scrubbed_fields": 0,
            "signatures_verified": 1,
        }

        inbound = _json.dumps({
            "trace_schema_version": "2.7.9",
            "events": [],
        }).encode("utf-8")

        with patch.object(persist_engine, "get_engine", return_value=engine):
            await _delegate_to_persist(inbound)

        engine.receive_and_persist.assert_called_once_with(inbound)
