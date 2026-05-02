# CIRISLens Trace Format — Lens-Specific Overlay

**Status:** Lens-specific overlay (storage / ingest / scrubbing)
**Canonical wire format:** [`CIRISAgent/FSD/TRACE_WIRE_FORMAT.md`](https://github.com/CIRISAI/CIRISAgent/blob/v2.7.8-stable/FSD/TRACE_WIRE_FORMAT.md) at tag `v2.7.8-stable`

---

## 0. Scope of this document

The trace **wire format** — top-level fields, component structure, per-event
shapes, signature canonicalization, wakeup trace types, dedup tuple — is owned
by `CIRISAgent/FSD/TRACE_WIRE_FORMAT.md` at the pinned tag above. **Don't
re-spec wire format here.** Three-copies-of-the-spec drift was how AV-9-style
regressions slipped past review (`agent_id_hash` getting silently dropped from
dedup keys); the lens repo no longer hosts a parallel copy.

What this doc covers — purely lens-side concerns the wire format doesn't speak to:

- §1 — what the lens **stores** at each `trace_level`
- §2 — audit-chain **verification procedure** the lens runs on ingest
- §3 — **TimescaleDB storage shape**
- §4 — **accord ingest endpoints**
- §5 — **PII scrubbing pipeline**
- §6 — references

If you came here looking for "what does CONSCIENCE_RESULT carry" / "how do I
verify a signature" / "what is the dedup tuple", read the canonical spec.

---

## 1. Trace detail levels — lens storage policy

The wire format defines the `trace_level` enum (`generic` | `detailed` |
`full_traces`). What the **lens** does at each level is a storage-policy
question:

| Level | What lens stores | What lens **scrubs** | What lens drops |
|-------|------------------|----------------------|-----------------|
| `generic` | Numeric scores, booleans, resource counters | (nothing — no text fields) | (nothing) |
| `detailed` | + identifiers (`thought_id`, `task_id`, `agent_name`), action types | (nothing — still no reasoning text) | (nothing) |
| `full_traces` | + reasoning text, prompts, context blobs | 21 text fields via NER + regex | Original pre-scrub content (only hash kept) |

`generic` traces power the public CIRIS Capacity Score; `detailed` is for
debugging without reasoning exposure; `full_traces` is the Coherence Ratchet
research corpus.

Mock-LLM traces (`models_used` contains "mock" case-insensitive) are dropped
at every level — production corpus stays clean of test traffic.

---

## 2. Audit-chain verification

The hash-chain algorithm is wire format. The lens's job is **running** it on
ingest:

1. Ed25519 signature over canonical components (algorithm per wire spec §5).
   Reject on fail; record `signature_verified=false` for the alerting path.
2. Hash-chain check on `audit_sequence_number` per agent: each
   `audit_entry_hash[n]` must match `SHA256(audit_entry_hash[n-1] || ...)`.
   Any gap or mismatch surfaces as a Coherence Ratchet `hash_chain_break`
   alert (see [`coherence_ratchet_detection.md`](./coherence_ratchet_detection.md)).
3. Per-agent dedup is enforced by the persistence layer (`ciris-persist`)
   using the wire-format-canonical tuple
   `(agent_id_hash, trace_id, thought_id, event_type, attempt_index, ts)` —
   AV-9 closure. If you're touching the dedup key, the rationale lives in
   `CIRISPersist/THREAT_MODEL.md §3.1` and the spec lives in the wire format.

---

## 3. Storage format

Production storage is **TimescaleDB** with denormalized columns for fast
analytical queries. Two tables back the live ingest path (post v0.2.x cutover):

```
cirislens.trace_events       -- one row per @streaming_step broadcast
                             -- dedup: (agent_id_hash, trace_id, thought_id,
                             --         event_type, attempt_index, ts)
cirislens.trace_llm_calls    -- one row per LLM call, parent_event_id → trace_events
```

Pre-cutover history lives in `cirislens.accord_traces` (renamed from the
legacy `covenant_traces`; backward-compatible view of the same name still
exists). Migration provenance: `sql/011_covenant_traces.sql`,
`sql/022_covenant_to_accord.sql`.

Sample lens-flavoured analytical query (denormalized columns, not wire shape):

```sql
SELECT trace_id, agent_id_hash, idma_k_eff, idma_phase
FROM cirislens.accord_traces
WHERE idma_fragility_flag = TRUE
  AND timestamp > NOW() - INTERVAL '24 hours'
ORDER BY idma_k_eff ASC;
```

Retention + compression policy is in [`../CLAUDE.md`](../CLAUDE.md) "Data
Retention Policies (Automatic)" — managed by TimescaleDB background jobs, not
spec'd here.

---

## 4. Ingest endpoints

```
POST /api/v1/accord/events            -- batch trace ingest (wire-format payload)
GET  /api/v1/accord/traces            -- query stored traces
POST /api/v1/accord/public-keys       -- register agent verification key
GET  /api/v1/accord/public-keys       -- list registered keys
```

Deprecated `/api/v1/covenant/*` endpoints remain available as backward-
compatible shims (`api/covenant_api.py` forwards to the accord routes); they'll
be removed once the agent fleet has fully cut over.

---

## 5. PII scrubbing for full_traces

`full_traces` carry reasoning text that may include PII. The lens scrubs at
ingest time **before storage**, preserving cryptographic provenance.

```
Agent sends full_trace → Verify Ed25519 sig → Hash original content
    → Scrub PII (NER + regex) → Sign scrubbed → Store only scrubbed
```

### Scrubbed fields (21 total)

| Component | Fields |
|-----------|--------|
| `THOUGHT_START` | `task_description`, `initial_context` |
| `SNAPSHOT_AND_CONTEXT` | `system_snapshot`, `gathered_context`, `relevant_memories`, `conversation_history` |
| `DMA_RESULTS` | `reasoning`, `prompt_used`, `combined_analysis` |
| `ASPDMA_RESULT` | `action_rationale`, `reasoning_summary`, `action_parameters`, `aspdma_prompt` |
| `CONSCIENCE_RESULT` | `conscience_override_reason`, `epistemic_data`, `updated_status_content`, `entropy_reason`, `coherence_reason`, `optimization_veto_justification`, `epistemic_humility_justification` |
| `ACTION_RESULT` | `execution_error` |

### Detection

- **NER** (spaCy `en_core_web_sm` + `xx_ent_wiki_sm`, ONNX INT8 fast path):
  `PERSON` → `[PERSON_1]`, `ORG` → `[ORG_1]`, plus `GPE` / `FAC` / `LOC` /
  `NORP`.
- **Regex:** email → `[EMAIL]`, phone → `[PHONE]`, IP → `[IP_ADDRESS]`, URL
  → `[URL]`, SSN → `[SSN]`, credit card → `[CREDIT_CARD]`.

### Cryptographic envelope (provenance preservation)

| Field | Purpose | Survives scrub-key loss? |
|-------|---------|--------------------------|
| `original_content_hash` | SHA-256 of pre-scrub content | ✅ |
| `signature` | Agent's original Ed25519 signature | ✅ |
| `signature_verified` | Whether agent signature was valid | ✅ |
| `scrub_timestamp` | When scrubbing occurred | ✅ |
| `scrub_signature_classical` | Lens steward Ed25519 signature of scrubbed content | ❌ |
| `scrub_signature_pqc` | Lens steward ML-DSA-65 signature (cold path) | ❌ |
| `scrub_key_id` | Steward identity used | ❌ |

**Key property:** even if the scrub signing key is lost, original-content
provenance is provable via `original_content_hash` + verified agent signature.
The scrub signature only provides tamper-evidence for the scrubbed version.

Steward identity is `lens-steward` (Ed25519 + ML-DSA-65 hybrid per
v0.2.x federation directory); see `api/persist_engine.py` and
[`../CLAUDE.md`](../CLAUDE.md) §"PII Scrubbing for Full Traces" for the
operational shape.

---

## 6. References

- [Coherence Ratchet Detection](./coherence_ratchet_detection.md) — anomaly detection on this corpus
- [CIRIS Scoring Specification](./ciris_scoring_specification.md) — Capacity Score derivation
- Canonical wire format: [`CIRISAgent/FSD/TRACE_WIRE_FORMAT.md @ v2.7.8-stable`](https://github.com/CIRISAI/CIRISAgent/blob/v2.7.8-stable/FSD/TRACE_WIRE_FORMAT.md)
- Persistence threat model (AV-9 dedup rationale): `CIRISPersist/THREAT_MODEL.md §3.1`
- Public-facing: [ciris.ai/how-it-works](https://ciris.ai/how-it-works/), [explore a trace](https://ciris.ai/explore-a-trace/), [privacy](https://ciris.ai/privacy)
