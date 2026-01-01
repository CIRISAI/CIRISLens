# CIRISLens Covenant Events Receiver - Integration Notes

**Date:** 2025-12-31 (Updated)
**From:** CIRISLens Team
**To:** CIRISAgent Team
**Re:** Logshipper SDK Integration Status

---

## Summary

CIRISLens now has a fully implemented `/v1/covenant/events` endpoint ready to receive Ed25519-signed reasoning traces from CIRIS agents. This implements the Coherence Ratchet receiver.

**New FSDs:**
- [Trace Format Specification](../FSD/trace_format_specification.md) - Canonical trace structure
- [Coherence Ratchet Detection](../FSD/coherence_ratchet_detection.md) - How anomalies are detected

## Endpoint Details

```
POST /api/v1/covenant/events
Content-Type: application/json
```

**Production URL:** `https://agents.ciris.ai/lens/api/v1/covenant/events`

### Request Format (Matches Agent SDK)

```json
{
  "events": [
    {
      "event_type": "complete_trace",
      "trace": {
        "trace_id": "trace-th_followup_th_std_2_b44ae980-20260101042008",
        "thought_id": "th_followup_th_std_2_b44ae980-ab8",
        "task_id": "VERIFY_IDENTITY_2e1cbbd8-e671-4124-884c-818a8aff9ce8",
        "agent_id_hash": "unknown",
        "started_at": "2026-01-01T04:20:08.026388+00:00",
        "completed_at": "2026-01-01T04:20:20.017550+00:00",
        "components": [
          {"component_type": "observation", "event_type": "THOUGHT_START", "timestamp": "...", "data": {...}},
          {"component_type": "context", "event_type": "SNAPSHOT_AND_CONTEXT", "timestamp": "...", "data": {...}},
          {"component_type": "rationale", "event_type": "DMA_RESULTS", "timestamp": "...", "data": {...}},
          {"component_type": "rationale", "event_type": "ASPDMA_RESULT", "timestamp": "...", "data": {...}},
          {"component_type": "conscience", "event_type": "CONSCIENCE_RESULT", "timestamp": "...", "data": {...}},
          {"component_type": "action", "event_type": "ACTION_RESULT", "timestamp": "...", "data": {...}}
        ],
        "signature": "DdP06mwFbKfQh0148pzgIITEc6V_MRmX-...",
        "signature_key_id": "wa-2025-06-14-ROOT00"
      }
    }
  ],
  "batch_timestamp": "2026-01-01T04:20:20.000000+00:00",
  "consent_timestamp": "2026-01-01T00:00:00Z"
}
```

### Response Format

**Success (200):**
```json
{
  "status": "ok",
  "received": 10,
  "accepted": 10,
  "rejected": 0
}
```

**Partial Success (200):**
```json
{
  "status": "partial",
  "received": 10,
  "accepted": 8,
  "rejected": 2,
  "rejected_traces": ["trace-id-1", "trace-id-2"],
  "errors": ["trace-id-1: Invalid signature", "trace-id-2: Unknown signer key"]
}
```

## Public Key Registration

Before agents can send signed traces, the root public key must be registered:

```
POST /api/v1/covenant/public-keys
{
  "key_id": "wa-2025-12-31-ROOT00",
  "public_key_base64": "<base64-encoded-32-byte-ed25519-pubkey>",
  "description": "Wakeup Authority root key"
}
```

**Action Required:** Please provide the root public key from `seed/root_pub.json` for registration in production CIRISLens.

## Agent SDK Compatibility

The receiver is designed to match the format sent by `CovenantMetricsService._send_events_batch()`:

| Agent SDK Field | CIRISLens Mapping |
|-----------------|-------------------|
| `events[].event_type` | `"complete_trace"` expected |
| `events[].trace.trace_id` | Stored as primary key |
| `events[].trace.components` | 6 components parsed and denormalized |
| `events[].trace.signature` | Base64 Ed25519 sig, verified against signer key |
| `events[].trace.signer_key_id` | Must match registered public key |
| `batch_timestamp` | Stored as trace timestamp |
| `consent_timestamp` | Stored for audit |

## Denormalized Fields

For fast dashboard and Coherence Ratchet queries, these fields are extracted:

### From Trace Top-Level:
- `thought_id`, `task_id` - Thought/task identification
- `agent_id_hash` - Anonymized agent ID
- `started_at`, `completed_at` - Timing

### From DMA_RESULTS:
- `csdma_plausibility_score` (0.0-1.0) - Common sense check
- `dsdma_domain_alignment` (0.0-1.0) - Domain alignment
- `dsdma_domain` - Domain name (e.g., "Datum")
- `pdma_stakeholders`, `pdma_conflicts` - Ethical analysis

### From ASPDMA_RESULT:
- `action_rationale` - Natural language justification (key for semantic analysis)
- `selected_action` (SPEAK, DEFER, PONDER, TASK_COMPLETE, etc.)

### From CONSCIENCE_RESULT:
- `conscience_passed` - Overall pass/fail
- `action_was_overridden` - Did conscience change the action?
- `entropy_level`, `coherence_level` (0.0-1.0) - Epistemic metrics
- `updated_status_detected`, `thought_depth_triggered` - Bypass guardrails
- `entropy_passed`, `coherence_passed`, `optimization_veto_passed`, `epistemic_humility_passed` - Ethical faculties

### From ACTION_RESULT:
- `action_success` - Execution success
- `audit_sequence_number`, `audit_entry_hash` - Hash chain verification
- `tokens_total`, `cost_cents`, `llm_calls`, `models_used` - Resource usage

### Trace Type Detection:
Inferred from `task_id` pattern:
- `VERIFY_IDENTITY_*` → VERIFY_IDENTITY
- `VALIDATE_INTEGRITY_*` → VALIDATE_INTEGRITY
- `EVALUATE_RESILIENCE_*` → EVALUATE_RESILIENCE
- `ACCEPT_INCOMPLETENESS_*` → ACCEPT_INCOMPLETENESS
- `EXPRESS_GRATITUDE_*` → EXPRESS_GRATITUDE

## Query Endpoints

**List traces:**
```
GET /api/v1/covenant/traces?trace_type=VERIFY_IDENTITY&limit=100
```

**List public keys:**
```
GET /api/v1/covenant/public-keys
```

## Database Schema

Created in `sql/011_covenant_traces.sql`:
- `covenant_public_keys` - Ed25519 key registry
- `covenant_traces` - TimescaleDB hypertable with 7-day chunks
- `covenant_trace_batches` - Batch ingestion audit
- `covenant_trace_metrics` - Prometheus-style metrics
- `covenant_traces_hourly` - Continuous aggregate for dashboards

**Retention:** 90 days detail, 1 year aggregates
**Compression:** After 7 days

## Testing Notes

The mock logshipper in `tools/qa_runner/server.py` confirms the agent is correctly:
1. Capturing all 6 trace components
2. Signing traces with Ed25519
3. Batching traces with consent timestamps
4. Sending to the configured endpoint

The 10 traces captured during QA runs match the expected format.

## Open Items

1. **Root Public Key:** Need the production key from `seed/root_pub.json` to enable signature verification
2. **Agent ID Hashing:** Currently using `trace_id[:16]` as agent hash. Confirm this matches the pre-hashed agent ID format from the SDK.
3. **Resilience Patterns:** The logshipper SDK has circuit breaker and backoff - CIRISLens returns 503 on DB unavailable to trigger retries.

## Contact

For integration issues, create a GitHub issue in CIRISLens or reach out to the observability team.

---

*This document generated for the Coherence Ratchet implementation milestone.*
