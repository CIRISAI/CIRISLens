# FSD: Lens-Core Adoption Gift Package

**Status:** Ready for handoff (2026-05-12)
**Owner (gift):** CIRISLens Python team
**Owner (recipient):** CIRISLensCore Rust team
**Cross-cutting:** CIRISEdge (transport substrate), CIRISPersist (storage substrate)

---

## 1. Why this document exists

CIRISLensCore has been gestating since 2026-05-03 as a single spec-scaffold
commit. Its `detector/`, `signing/`, and `observability/` modules are 2-LOC
stubs. Its persist pin is `v0.4.2` — eight releases behind `v0.5.8` that lens
just adopted.

The reason for the stall is sound: don't write Rust until the Python
reference has shaken out the algorithm contracts against real persist + real
federation traffic. That order is finally satisfied. Persist v0.5.8 closes
CIRISPersist#23 with the full §A–§I read substrate; the Python lens-API
exercises every primitive end-to-end against live prod (45+ probes, 100%
pass on functional substrate per the validation matrix in
`docs/persist_v0_5_8_substrate_validation.md`).

This document is the inventory of **what to port and where to find the
executable Python reference**, so the Rust port reads as transpilation
rather than as new design. Per `~/CIRISLensCore/MISSION.md`: *"lens-core is
a function any peer can run on data the peer already has — a library, not
a service."*

## 2. Substrate state at handoff

### CIRISPersist v0.5.8 (PyPI live)

Every read primitive lens-core needs ships as a typed Rust function +
PyO3 wrapper, surface-dual per the v0.4.1 verify-primitive precedent.
Lens-core consumes via the rlib path (in-process Rust, no PyO3 hop);
lens-API consumes via the PyO3 wrapper.

| § | Primitive | Rust trait method | Lens Python pass-through |
|---|---|---|---|
| A | `list_trace_summaries` | `ReadEngine::list_trace_summaries(filter, cursor, limit)` | `accord_api.py:list_repository_traces` |
| A | `get_trace_summary` | `ReadEngine::get_trace_summary(trace_id)` | (composed in §B) |
| B | `get_trace_detail` | `ReadEngine::get_trace_detail(trace_id)` | `accord_api.py:get_repository_trace` |
| C | `list_tasks` | `ReadEngine::list_tasks(filter, cursor, limit)` | `accord_api.py:list_tasks` |
| D | `list_llm_calls` | `ReadEngine::list_llm_calls(filter, cursor, limit)` | `accord_api.py:list_llm_calls` |
| D | `aggregate_llm_costs` | `ReadEngine::aggregate_llm_costs(filter)` | `accord_api.py:aggregate_llm_costs` |
| E | `aggregate_scoring_factors` | `ReadEngine::aggregate_scoring_factors(aid, window, baseline?)` | `accord_api.py:scoring_aggregate_factors` |
| E | `aggregate_scoring_factors_batch` | (batch variant) | `accord_api.py:scoring_aggregate_factors_batch` |
| E | `count_traces` / `count_overrides` / `count_identity_changes` / `aggregate_audit_chain` | granular helpers | (granular not yet exposed lens-side; persist primitives ready) |
| F | `cross_agent_divergence` | `ReadEngine::cross_agent_divergence(domain, window, metric)` | `accord_api.py:ratchet_cross_agent_divergence` |
| F | `temporal_drift` | `ReadEngine::temporal_drift(aid, baseline, comparison)` | `accord_api.py:ratchet_temporal_drift` |
| F | `hash_chain_gaps` | `ReadEngine::hash_chain_gaps(aid, window)` | `accord_api.py:ratchet_hash_chain_gaps` |
| F | `conscience_override_rates` | `ReadEngine::conscience_override_rates(domain, window)` | `accord_api.py:ratchet_conscience_override_rates` |
| G | `corpus_shape` | `ReadEngine::corpus_shape(filter)` | `accord_api.py:corpus_shape` |
| H | `aggregate_scrub_stats` | `ReadEngine::aggregate_scrub_stats(window)` | `accord_api.py:aggregate_scrub_stats` |
| I | `list_federation_keys` | `ReadEngine::list_federation_keys(filter, cursor, limit)` | `accord_api.py:list_federation_keys` |
| I | `list_attestations` | `ReadEngine::list_attestations(filter, cursor, limit)` | `accord_api.py:list_attestations` |
| I | `list_revocations` | `ReadEngine::list_revocations(filter, cursor, limit)` | `accord_api.py:list_revocations` |

Hardening (v0.5.3 + v0.5.4): `panic = "unwind"` in release, `PgRowExt::safe_get`
across ~155 Row::get sites, `pyo3::create_exception! LensQueryError` wrapping
all 70+ PyO3 entry points via `catch_panic`. Rust-side bugs surface as
typed errors, never SIGABRT. Python regression test `tests/python/
test_catch_panic.py` (5 cases) gates the invariant on every persist CI run.

### CIRISEdge v0.1.1 (workspace member; not on crates.io)

Already shipped per `~/CIRISEdge/src/lib.rs`:

- `Edge` / `EdgeBuilder` — top-level construction and lifecycle.
- `Edge::register_handler<M, H>` — typed handler registration; lens-core
  registers an `AccordEventsBatch` handler.
- `Edge::run(shutdown_rx)` — runtime loop; spawns transport listeners and
  inbound dispatch.
- `Transport` trait + `HttpTransport` impl with **`POST /edge/inbound`
  TCP listener** on `0.0.0.0:8080` (default, configurable) via axum.
  `MAX_BODY_BYTES = 8 MiB` enforced at the extractor (AV-13).
- `VerifyPipeline` + `VerifyDirectory` + `HybridPolicy` — verify-via-persist
  before handlers see bytes. Edge does NOT re-verify in handler; lens-core
  trusts the type-system attestation.
- `VerifiedTrace` (type alias for `VerifiedEnvelope`) — the post-verify
  typed payload lens-core consumes.
- `StewardSigner` — Ed25519 + ML-DSA-65 outbound signing.
- `Handler` / `Message` / `Delivery` / `DurableHandle` / `DurableOutcome`
  — typed delivery-class enforcement (OQ-09 closure).

The TCP-binding plumbing the user flagged as a prerequisite is therefore
**already shipped**. Lens-core's open work is consuming this surface, not
asking edge to grow it.

One pin nit: edge's `Cargo.toml` still points at `ciris-persist = v0.4.2`.
Pre-§A read substrate. Edge doesn't strictly need the read primitives
(its job is verify + transport + outbound), but the dep-tree-single-version
discipline that v0.4.5 noted breaks once consumers (lens-core) pull in
both edge and a newer persist. Bumping edge's persist pin to v0.5.8 is
a clean follow-up — filed separately.

## 3. The five gifts

### Gift 1 — Persist read-primitive consumption patterns

**Where to find the spec:** `CIRISLens/api/accord_api.py` lines 3189–3938
(§A through §I pass-through endpoints) and lines 3672–3848 (§E/§F helper
`_window_pair_jsons`, `_engine_or_503`, `_hours_to_window_json`).

**What the Rust port should mirror:**

- Filter construction: dict-built JSON, optional fields skipped, value
  validation via the typed Rust filter struct on persist side. The
  Python side never validates filter shape — persist's deserialize gate
  is the truth.
- Time-window discipline: `_window_pair_jsons(scoring_hours, baseline_hours)`
  anchors both windows on a single `datetime.now(UTC)` snapshot so the
  ranges are contiguous (no microsecond drift). Direct port; the same
  anti-pattern exists in any async runtime.
- Error mapping: `ValueError → HTTP 400` (caller-fault), `RuntimeError →
  HTTP 503` (operator-fault), `LensQueryError` (not yet caught explicitly
  — propagates to FastAPI's default → uvicorn 500; tighter routing is a
  follow-up). The Rust port shapes the same kinds (`ReadError::InvalidArgument
  → 400`, `ReadError::Backend → 503`, panic-caught → 500).
- Cursor pagination: opaque JSON-string cursors, never decoded by lens.
  Page-1 omits `cursor=`; subsequent pages pass back what `next_cursor`
  returned. `next_cursor = null` is the terminal signal.
- Limit caps: lens-API caps every list endpoint's `limit` to a sensible
  ceiling (typically 1000) before calling persist. Same discipline in lens-core.

### Gift 2 — Coherence Ratchet detection algorithm reference

**Where:** `CIRISLens/api/analysis/coherence_ratchet.py` (refactored in
commit `a862c25` to consume §F primitives end-to-end).

**Target module:** `~/CIRISLensCore/src/detector/mod.rs` (currently 2 LOC).

**Four detection methods** map 1:1 onto persist §F primitives:

| Python method | Persist primitive | Severity rule |
|---|---|---|
| `_detect_cross_agent_divergence_via_persist` | `cross_agent_divergence(domain, window, metric)` | `|z| > Z_SCORE_WARNING` (2σ) → warning; `> Z_SCORE_CRITICAL` (3σ) → critical |
| `_detect_temporal_drift_via_persist` | `temporal_drift(aid, baseline, comparison)` | same z-score thresholds |
| `_detect_hash_chain_anomalies_via_persist` | `hash_chain_gaps(aid, window)` | any gap → critical (audit-trail integrity is non-negotiable) |
| `_detect_conscience_override_anomalies_via_persist` | `conscience_override_rates(domain, window)` | `multiple_of_domain_avg > 2x` → warning; `> 3x` → critical |

**Discovery helpers** the Python module adds (persist doesn't expose a
distinct-value primitive in v0.5.8; sample-the-recent-corpus pattern
applies):

- `_scan_summaries(limit=1000)` — paged list_trace_summaries scan
- `_enumerate_deployment_domains()` — distinct `deployment_domain` values
- `_enumerate_agents()` — distinct `agent_id_hash` values

When persist exposes a typed `distinct_*` primitive (§J?), drop these
helpers and call directly.

**Stratification correction (important for the port):** the legacy
lens-side SQL stratified by `dsdma_domain` (the agent's reasoning DMA
domain). Persist §F stratifies by `deployment_domain` (cohort identity).
`deployment_domain` is the federation-uniform stratification — agents in
the same production environment should produce similar score distributions;
outliers indicate behavioral drift. Don't preserve the legacy stratification
in the Rust port.

**Out of scope for §F port:** `detect_intra_agent_inconsistency` has no §F
equivalent (it could be modeled as multiple `temporal_drift` calls over
narrow windows; defer until persist exposes the inputs natively).

### Gift 3 — Capacity scoring composition reference (refactor pending)

**Where:** `CIRISLens/api/scoring.py` (494 LOC, 5 factor functions) +
`api/ciris_scoring.py` (937 LOC, expanded version with sub-component
breakdowns).

**Target module:** `~/CIRISLensCore/src/scoring/mod.rs` (currently 93 LOC).

**Status:** scoring.py still on raw SQL against the dead `accord_traces`
table. Stage-5 refactor (consume §E `aggregate_scoring_factors`) is queued
but not landed. Until the refactor commits, the Rust port can read the
FORMULA from the existing Python code and the INPUT MAPPING from
`src/read/scoring.rs:ScoringFactorAggregate` directly; the Python is just
slow to land its persist-side rewrite.

**Five-factor composition** (formulas from scoring.py:50–60 constants and
calculate_*_score bodies):

| Factor | Formula | Inputs (from `ScoringFactorAggregate`) |
|---|---|---|
| C (Core Identity) | `exp(-LAMBDA_C * D_identity) * exp(-MU_C * K_contradiction)` | `identity_changes / trace_count`, `conscience_overrides / trace_count` |
| I_int (Integrity) | `I_chain * I_coverage * I_replay` | `(audit_chain_total - audit_chain_gaps) / audit_chain_total`, `audit_signed_total / trace_count` |
| R (Resilience) | `1 - drift_penalty` (absolute-change thresholds) | `drift_z_score`, `recovery_events` (override→next-pass latencies) |
| I_inc (Incompleteness Awareness) | `(1 - ECE) * Q_deferral * (1 - U_unsafe)` | `calibration_error`, `unsafe_action_rate` |
| S (Sustained Coherence) | `S_base * (1 + w_pm * P_positive) * (1 + w_ef * P_ethical)` (time-decayed) | `coherence_decay_series` (per-hour rates with decay weight) |
| Composite | `C * max(I_int, 0.1) * R * I_inc * max(S, 0.1)` | (floors prevent collapse from single-zero factor) |

Constants per `scoring.py:21–23`:

```python
LAMBDA_C = 5.0        # Sensitivity to identity drift [2, 10]
MU_C = 10.0           # Sensitivity to contradiction [5, 20]
DECAY_RATE = 0.05     # Daily decay rate for coherence [0.02, 0.10]
MIN_TRACES_FOR_SCORING = 30
MIN_DAYS_FOR_BASELINE = 7
```

**Batch sweep:** `aggregate_scoring_factors_batch(aids, window, baseline?)`
returns one aggregate per agent in input order — one DB round-trip for a
fleet sweep, not N. The Rust port should use the batch primitive for
fleet capacity scoring; the single-agent primitive only for per-request
scoring.

### Gift 4 — PII scrubber + extraction patterns

**Already adopted.** `~/CIRISLensCore/src/scrub/` (2773 LOC) and
`src/extract/` (670 LOC) were lifted from the in-tree
`CIRISLens/cirislens-core/src/scrubber/` per the May-3 scaffold commit
message ("gifted scrubber/extraction patterns"). No new work in this
gift package.

**Caveat surfaced during v0.5.8 validation:** §A `TraceSummary.agent_id_hash`
returns the literal string `[IDENTIFIER]` for a subset of Ally's
`detailed`-level traces. The lens-side NER scrubber is misidentifying
hash-shaped strings in the trace payload and the agent_id_hash column
denormalization is consuming the scrubbed value. **The Rust port MUST NOT
scrub `agent_id_hash`** — it is the federation identity key (AV-9 dedup-tuple
prefix). Whichever scrubber path is doing this in lens needs a per-field
allowlist; same allowlist needs to land in the lens-core port. Tracking
this as a separate finding against the in-tree cirislens-core scrubber
(not against persist).

### Gift 5 — HTTP edge surface contract + legacy-stamp shim

**Where:** `CIRISLens/api/accord_api.py:receive_accord_events` (line 2011),
`_delegate_to_persist` (line 1904), `_rewrite_legacy_schema_stamp` (line 1844).

**Target shape post-cutover:**

```rust
// lens-core registers itself as the accord-events handler
edge.register_handler::<AccordEventsBatch, _>(LensCoreHandler::new(
    persist_engine.clone(),
)).await?;

// edge owns: TCP bind → axum POST /edge/inbound → AV-13 size cap →
//            verify_hybrid_via_directory (via persist directory) →
//            VerifiedTrace dispatch

// lens-core handles: VerifiedTrace → pre_verify_rewrite (legacy stamp shim)
//                    → persist.receive_and_persist(canonical_bytes)
//                    → ratchet hot path (post-ingest detection)
```

**The legacy-stamp rewrite (commit `2434273`):** pre-2.7.8.9 emitters
stamp `trace_schema_version: "2.7.0"` envelope-side but sign only the
2-field legacy canonical. Persist's by-stamp dispatch routes `"2.7.0"`
to the 9-field canonicalizer → strict-verify fails. The lens-Python shim
flips the envelope stamp to `"2.7.legacy"` before delegation; persist's
2.7.legacy arm routes correctly.

Port target: a `pipeline::lifecycle::pre_verify_rewrite` step in lens-core
that runs the same flip on raw bytes before edge's verify pipeline sees
them. Sunset condition unchanged — drop the shim once
`federation_canonical_match_total{wire="2.7.legacy"} = 0` through a
7-day soak (per persist `src/schema/version.rs:36`).

## 4. The cutover sequence

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 0 — TODAY                                                     │
│                                                                     │
│   Agent ─HTTP─► Python lens-API (FastAPI/uvicorn)                   │
│                 │                                                   │
│                 ├─ _rewrite_legacy_schema_stamp (CIRISLens#9 shim)  │
│                 ├─ _delegate_to_persist                             │
│                 │                                                   │
│                 ▼                                                   │
│              persist.Engine (PyO3) ─► cirislens.trace_events        │
│                                                                     │
│   Python lens-API ALSO serves: §A-§I read pass-throughs (1caf9c0)   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 1 — Rust ingest handler lands (lens-core consumes edge)       │
│                                                                     │
│   Agent ─HTTP─► CIRISEdge HttpTransport (axum, 0.0.0.0:8080)        │
│                 │                                                   │
│                 ├─ AV-13 body cap                                   │
│                 ├─ VerifyPipeline (hybrid via persist directory)    │
│                 ▼                                                   │
│              VerifiedTrace ─► LensCoreHandler::handle                │
│                                ├─ pre_verify_rewrite                │
│                                ├─ persist.Engine (rlib, NO PyO3)    │
│                                ▼                                    │
│                             cirislens.trace_events                  │
│                                                                     │
│   Python lens-API: still serves §A-§I reads + admin endpoints       │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 2 — Lens-core grows read surface                              │
│                                                                     │
│   lens-core adds its own axum app for §A-§I; Python lens-API        │
│   becomes optional. Migration is per-endpoint; consumers (website,  │
│   bridge dashboards) re-point HOST.                                 │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 3 — Agent-fold (PoB §3.1)                                     │
│                                                                     │
│   Every CIRISAgent links lens-core as rlib. Detection + scoring     │
│   run on the agent's own hot path against its own persist DB.       │
│   Federation peers cross-validate via persist's federation tables.  │
│   Python lens-API retires entirely.                                 │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. CIRISLensCore implementation checklist

In priority order (lower priority blocks higher):

- [ ] **Bump `Cargo.toml` persist pin v0.4.2 → v0.5.8** + edge pin to v0.1.1
      (already on v0.1.1, but persist transitive needs alignment).
- [ ] **Implement `LensCoreHandler` in `src/pipeline/lifecycle.rs`** —
      receives `VerifiedTrace`, applies `_rewrite_legacy_schema_stamp`
      port, calls `persist.receive_and_persist`. Bootstraps Phase 1.
- [ ] **Port the four §F detectors** to `src/detector/mod.rs` per Gift 2.
      Drop the 2-LOC stub.
- [ ] **Port the five-factor scoring composition** to `src/scoring/mod.rs`
      per Gift 3. Consume §E `aggregate_scoring_factors_batch` for fleet
      sweeps.
- [ ] **Wire `src/signing/mod.rs`** to `persist::StewardSigner` for
      detection-event signing. Drop the 2-LOC stub.
- [ ] **Implement `src/observability/mod.rs`** — `federation_canonical_match_total`
      counter for the §F sunset rule, plus the standard request-rate /
      latency metrics. Drop the 2-LOC stub.
- [ ] **Per-field scrubber allowlist** — ensure `agent_id_hash` is NEVER
      scrubbed (AV-9 invariant). Mirror the fix in the in-tree
      `CIRISLens/cirislens-core/` scrubber as a defense-in-depth.
- [ ] **Integration test against the 45-probe matrix** that validated
      lens-API on v0.5.8 (`docs/persist_v0_5_8_substrate_validation.md`).
      lens-core's outputs MUST match lens-API's outputs byte-for-byte
      on the same input corpus (modulo timestamp + UUID drift).

## 6. CIRISEdge ask (the only outstanding edge-side gap)

**Bump `Cargo.toml` persist pin v0.4.2 → v0.5.8** in `~/CIRISEdge`.

Edge's runtime API doesn't consume the new read primitives — its job is
verify (uses `verify_hybrid_via_directory`, present since persist v0.4.1)
+ transport + outbound queue (all present in v0.4.0). But edge's
transitive persist pin being 6 versions behind means anyone pulling
`ciris-edge = v0.1.1` AND `ciris-persist = v0.5.8` into the same Cargo
graph hits a dep-resolution warning at minimum, and a single-version
violation at worst. Bumping edge's pin matches the persist v0.4.5 / v0.5.2
discipline that explicitly tracks dep-tree single-versioning.

No code change required in edge — just a `Cargo.toml` tag bump and a
fresh `cargo build` smoke test.

## 7. Threat model invariants the port must preserve

| AV | Invariant | Where it's enforced in Python today |
|---|---|---|
| AV-9 | Trace-scoped reads gate on `agent_id_hash` at caller's auth layer | `accord_api.py:_engine_or_503` is the boundary; caller-side auth is upstream of lens |
| AV-13 | `MAX_BODY_BYTES = 8 MiB` at the HTTP extractor | `api/main.py:cache_request_body` middleware + edge's `HttpTransport::listen` |
| AV-15 | Stable error-kind tokens at FFI; no attacker-controlled strings | `_delegate_to_persist` catches `ValueError` (kind) + `RuntimeError` (kind); persist's `e.args[0]` is the stable token |
| AV-43 | Aggregates return statistics not content; caller applies k-anonymity | All §E/§G/§H endpoints surface `sample_count` / `trace_count` explicitly so the caller can gate |
| AV-44 | `panic = "unwind"` + `catch_panic` + `LensQueryError` | Persist v0.5.3+; lens-core port must keep both flags in its own `Cargo.toml` release profile |

## 8. Validation acceptance (when can lens-API retire?)

1. lens-core handles POST `/edge/inbound` and writes to persist with **zero
   Python in the path** for ingest.
2. lens-core's detector module produces `AnomalyAlert` objects matching
   the Python `coherence_ratchet.py` output on the same input corpus
   (assertion: same alerts with same `severity` + `metric` + `agent_id_hash`
   within a 1-second timestamp tolerance).
3. lens-core's scoring module produces `AgentScore` values matching
   `scoring.py` output on the same input corpus (assertion: same
   `capacity_score` within `1e-6` floating-point tolerance, same
   `data_sufficiency` label).
4. Python `_rewrite_legacy_schema_stamp` is ported to lens-core
   pre-verify pipeline; the matrix from `commit 2434273` (Scout's
   2.7.legacy traffic) lands cleanly through the Rust path.
5. Full §A-§I read endpoints either re-implemented in lens-core's axum
   surface OR Python lens-API kept ONLY as the HTTP-read facade (operator
   choice).
6. The 45-probe matrix (`docs/persist_v0_5_8_substrate_validation.md`)
   runs cleanly against lens-core's surface.

## 9. References

- CIRISPersist v0.5.0–v0.5.8 release notes — `~/CIRISPersist/CHANGELOG.md` (commit-message format)
- CIRISPersist FSD — `~/CIRISPersist/FSD/V0_5_0_FEDERATION_READ_PRIMITIVES.md`
- CIRISPersist THREAT_MODEL — `~/CIRISPersist/docs/THREAT_MODEL.md` §3.13 + AV-43, AV-44
- CIRISEdge crate skeleton — `~/CIRISEdge/src/lib.rs`, FSD `~/CIRISEdge/FSD/CIRIS_EDGE.md`
- CIRISLensCore mission + scaffold — `~/CIRISLensCore/MISSION.md`, `~/CIRISLensCore/src/lib.rs`
- This document's canonical home — `~/CIRISLens/FSD/LENS_CORE_ADOPTION_GIFT_PACKAGE.md`
- Coherence-ratchet refactor commit — CIRISLens `a862c25`
- §A/§B pass-through commit — CIRISLens `ad5d574`
- §E/§F pass-through commit — CIRISLens `80a20e6`
- §C/§D/§G/§H/§I pass-through commit — CIRISLens `1caf9c0`
- v0.5.8 substrate validation — CIRISLens session log (this conversation)
