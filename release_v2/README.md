# CIRIS Reasoning Trace Corpus v2 — QA-eval at wire format 2.7.9

Per-event reasoning traces from production CIRIS agents at wire format
v2.7.9, filtered to QA-evaluation traffic for downstream validation work.

**Status:** active; corpus snapshots produced quarterly (or on demand
via `scripts/export_qa_27_9.py`). First snapshot triggered after the
2.7.9 cutover went live (`cirislens:2b0c129` deploy + persist v0.3.2).

## What's different from v1

| Dimension | v1 (`data_scrubbed_v1`) | v2 (this corpus) |
|-----------|------------------------|------------------|
| Schema | `accord_traces` (per-thought rows, lens-authored) | `trace_events` + `trace_llm_calls` (per-event, persist-authored) |
| Wire format | mixed pre-2.7.9 | strictly `schema_version = '2.7.9'` |
| Dedup tuple | (trace_id) — convention-only | (agent_id_hash, trace_id, thought_id, event_type, attempt_index, ts) — structurally bound, AV-9 closed |
| LLM-call linkage | denormalized into per-thought rows | first-class `trace_llm_calls` rows joined via (trace_id, parent_event_type, parent_attempt_index) |
| Trace-level scope | mixed `generic`/`detailed`/`full_traces` | `detailed` + `full_traces` only (`generic` is content-free) |
| Traffic subset | QA + organic users | QA-evaluation only (`channel_id LIKE 'model_eval_%'`) |
| PQC | Ed25519-only signatures | Ed25519 + ML-DSA-65 hybrid (federation_keys provenance) |
| Authoritative wire spec | (see corpus) | `CIRISAgent/FSD/TRACE_WIRE_FORMAT.md @ v2.7.9-stable` |

## Files in each snapshot

```
data/<UTC-timestamp>/
  trace_events.jsonl         per-event rows, ORDER BY trace_id, ts, event_id
  trace_llm_calls.jsonl      per-LLM-call rows, ORDER BY trace_id, ts, call_id
  accord_public_keys.jsonl   for signature_key_id → public_key_base64 resolution
  MANIFEST.json              sha256 + row counts + filter params + wire spec pin
```

JSONL is one JSON object per line, written with `sort_keys=True` and
compact separators so two exports of the same data have byte-identical
output (sha256-comparable).

## Provenance preservation

Every `trace_events` row carries the agent's Ed25519 signature
(`signature` + `signing_key_id`) over canonical bytes derived from the
row's `trace_level` and `payload` per the wire-format spec at the
pinned tag. The export does **not** re-canonicalize on write — persist's
canonicalization stays authoritative ([CIRISPersist#7](https://github.com/CIRISAI/CIRISPersist/issues/7)
lesson: byte-stable crypto behavior belongs in one place; consumers
don't reimplement).

To verify:

1. Resolve `signature_key_id` → `public_key_base64` via
   `accord_public_keys.jsonl`.
2. Reconstruct canonical bytes per the wire-format spec at
   `CIRISAgent/FSD/TRACE_WIRE_FORMAT.md @ v2.7.9-stable`.
3. Verify Ed25519(signature, public_key, canonical_bytes).

The public key directory IS in this corpus; the wire-format spec is
externally pinned. No additional artifacts needed for verification.

## Filter rationale

**Why `schema_version = '2.7.9'` (not earlier).** Pre-2.7.9 traces have
nullable `parent_event_type` / `parent_attempt_index` and envelope-only
`agent_id_hash`, which degrades parent-linkage analytics and AV-9
dedup-key analytics. Mixing versions in a single corpus makes
detector-math calibration ambiguous; pinning to 2.7.9 keeps the
analytical surface coherent.

**Why `detailed` + `full_traces`, not `generic`.** Generic-level traces
carry only numeric scores and booleans — no structured `data` payloads,
no reasoning content. RATCHET's reasoning-topology features (H1–H7
detectors) need the structured payloads, which start at `detailed`.
Generic traces are not omitted from the source database; they're just
out of scope for this corpus.

**Why `model_eval_*` channels.** QA-evaluation traffic has
high prompt-variance across language × question, which is exactly the
substrate constraint-space topology analyses need. Honest agents
produce varied reasoning patterns under varied prompts; coordinated
adversaries (Sybil archetypes) tend to lockstep. Organic real-user
traffic is privacy-sensitive and lower-variance per agent — out of
scope here, in scope for [v1](https://huggingface.co/datasets/CIRISAI/data_scrubbed_v1).

## PII scrubbing

`detailed` traces don't carry reasoning text. `full_traces` are scrubbed
by lens at ingest before storage — the lens process never persists the
pre-scrub content; only the SHA-256 of pre-scrub bytes survives for
provenance. See `FSD/trace_format_specification.md` §5 for the
scrubbing pipeline detail; the v2.7.9 wire format spec at
`CIRISAgent/FSD/TRACE_WIRE_FORMAT.md` for the cryptographic envelope.

## Schema

See `CIRISPersist/docs/PUBLIC_SCHEMA_CONTRACT.md` at v0.3.2 for column-level
stability tiers. Quick summary of what each table carries:

- **`trace_events`** — one row per `@streaming_step` broadcast (THOUGHT_START,
  SNAPSHOT_AND_CONTEXT, DMA_RESULTS, ASPDMA_RESULT, IDMA_RESULT,
  CONSCIENCE_RESULT, ACTION_RESULT, LLM_CALL, VERB_SECOND_PASS_RESULT).
  `payload` is event-specific JSONB; shape varies by `event_type`.
- **`trace_llm_calls`** — one row per LLM invocation, joined to its parent
  trace_events row via `(trace_id, parent_event_type, parent_attempt_index)`.
  Carries model/provider/cost/latency.
- **`accord_public_keys`** — Ed25519 verification keys per `signing_key_id`.
  Will be replaced by `federation_keys` at v0.4.0; v2 corpus snapshots
  will gain a `federation_keys.jsonl` companion in that release window.

## How to regenerate

```bash
CIRISLENS_READ_DSN=postgres://cirislens_analytics:...@db/cirislens \
    python scripts/export_qa_27_9.py \
        --output-dir release_v2/data/$(date -u +%Y-%m-%dT%H-%M-%SZ)/
```

The export script lives in `scripts/export_qa_27_9.py` in this repo.
Streams via cursor; bounded memory regardless of corpus size.

## License

apache-2.0 (matches v1 corpus). See `LICENSE`.

## References

- Wire format spec: [`CIRISAgent/FSD/TRACE_WIRE_FORMAT.md @ v2.7.9-stable`](https://github.com/CIRISAI/CIRISAgent/blob/v2.7.9-stable/FSD/TRACE_WIRE_FORMAT.md)
- Persist schema contract: [`CIRISPersist/docs/PUBLIC_SCHEMA_CONTRACT.md`](https://github.com/CIRISAI/CIRISPersist/blob/v0.3.2/docs/PUBLIC_SCHEMA_CONTRACT.md)
- v1 corpus precedent: [`CIRISAI/data_scrubbed_v1`](https://huggingface.co/datasets/CIRISAI/data_scrubbed_v1)
- Lens FSD overlay: [`FSD/trace_format_specification.md`](../FSD/trace_format_specification.md)
- Issue: [`CIRISLens#4`](https://github.com/CIRISAI/CIRISLens/issues/4)
