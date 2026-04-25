# CIRISLens

**Rust-edge observability for CIRIS agents — ingest signed reasoning traces at scale, tune the Coherence Ratchet, measure agent health.**

## What CIRISLens does

CIRISLens receives Ed25519-signed reasoning traces from CIRIS agents in the field, verifies and scrubs them at the edge, and stores the signal in TimescaleDB. The corpus powers two things:

1. **Coherence Ratchet** — anomaly detection over reasoning behavior (phase transitions, cross-agent divergence, hash-chain integrity, conscience-override rate).
2. **CIRIS Capacity Score** — a composite trustworthiness score per agent, computed from privacy-safe numeric signals only.

The hot path is a Rust core (`cirislens-core`, compiled into the API via PyO3). Schema validation, Ed25519 signature verification, field extraction, and PII scrubbing all run in Rust; Python handles routing, storage, and dashboards.

## The Rust edge

```
agent  ─signed trace─►  [ cirislens-core / Rust ]  ─►  TimescaleDB  ─►  Grafana
                       • schema validation
                       • Ed25519 verify
                       • PII scrub (NER + regex)
                       • field extraction
                       • security sanitization
```

Why Rust at the edge:

- **Throughput**: trace batches arrive at scale; schema validation and signature verification can't be the bottleneck.
- **Correctness under adversarial input**: XSS / SQLi / command-injection sanitizers run in a memory-safe language with no silent truncation.
- **One verification boundary**: traces verify once in Rust. Everything downstream trusts the envelope.

Falls back cleanly to a Python implementation when the Rust core isn't built (development convenience).

## Coherence Ratchet

Five detection mechanisms run over the trace corpus:

| Mechanism | What it catches |
|-----------|-----------------|
| Cross-agent divergence | An agent's DMA scores drift from peers in the same domain |
| Intra-agent consistency | Self-contradictory reasoning within an agent |
| Hash-chain verification | Gaps or tampering in the signed audit trail |
| Temporal drift | Sudden behavioral changes over time |
| Conscience override rate | Elevated ethical-faculty intervention rates |

Detection is **triage, not verdict**. Anomalies warrant human review; they don't prove misalignment.

See [docs/coherence-ratchet/](docs/coherence-ratchet/) for mechanism details.

## CIRIS Capacity Score

Composite trustworthiness, five factors, computable from `generic` traces (no reasoning text needed):

| Factor | Measures |
|--------|----------|
| **C** — Core Identity | Identity stability, contradiction rate |
| **I_int** — Integrity | Signature verification, field coverage |
| **R** — Resilience | Score drift, recovery time |
| **I_inc** — Incompleteness | Calibration, deferral quality |
| **S** — Sustained Coherence | Coherence decay, cross-agent validation |

See [FSD/ciris_scoring_specification.md](FSD/ciris_scoring_specification.md).

## Trace levels

Agents emit at three privacy tiers, opt-in controlled:

| Level | Contains | PII |
|-------|----------|-----|
| `generic` | Numeric scores only | None |
| `detailed` | + identifiers, timestamps, action types | Low |
| `full_traces` | + reasoning text, prompts, context | Auto-scrubbed |

Full traces are PII-scrubbed in Rust at ingest (NER via spaCy for persons/orgs/locations, regex for emails/phones/IPs/SSNs/cards). The original content hash is preserved as cryptographic provenance; the scrubbed version is re-signed by a CIRISLens key.

Traces from mock/test LLMs are automatically routed to a separate mock repository to keep the production corpus clean.

## Quick start

```bash
# Prereqs: Docker, Docker Compose, 4GB RAM minimum
git clone https://github.com/CIRISAI/CIRISLens.git
cd CIRISLens
cp .env.example .env   # add agent tokens (never commit)
docker compose -f docker-compose.managed.yml up -d
```

Access:
- **Grafana**: http://localhost:3000 (Google OAuth in prod, `admin/admin` locally)
- **Admin UI**: http://localhost:8080/cirislens/admin/
- **API health**: http://localhost:8000/health

Add agent tokens via the Admin UI → Tokens tab (write-only, not recoverable after save).

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  cirislens-api  (FastAPI + embedded Rust core)                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ cirislens-core  (Rust / PyO3)                            │ │
│  │ • schema validation   • Ed25519 verify                   │ │
│  │ • PII scrub           • field extraction                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│  Python routes: ingest, admin, scoring, ratchet               │
└──────────────────┬────────────────────────┬───────────────────┘
                   │                        │
                   ▼                        ▼
           TimescaleDB                   Grafana
           (hypertables,              (CIRIS dashboards)
            compression,
            retention)
```

| Component | Purpose | Port |
|-----------|---------|------|
| cirislens-api | Ingest, admin, scoring, ratchet | 8000 |
| TimescaleDB | Hypertables + compression + retention | 5432 |
| Grafana | Visualization | 3000 |

## Data retention

Automatic via TimescaleDB jobs:

| Table | Detail retention | Compression | Continuous aggregates |
|-------|------------------|-------------|-----------------------|
| `accord_traces` | 30 days | after 7 days | hourly (90 d), daily (1 yr) |
| `service_logs` | 14 days | after 7 days | — |
| Agent metrics | 30 days | after 7 days | hourly + daily |

## Analysis tooling

Working with the trace corpus for research:

```bash
# Print a faceted shape card before any analysis (task class, language,
# region, agent, stationarity check) — prevents misreading QA cycles as
# agent behavior
scripts/corpus_shape.py --window 24h

# Export the full corpus as JSONL for offline analysis
scripts/export_corpus.sh ~/my-analysis-dir
```

Analysis queries should read the `cirislens.trace_context` view, not raw
`accord_traces` — the view surfaces `task_class`, `qa_language`,
`qa_question_num`, coarsened region, and primary model as native columns.

## Privacy posture

- Grafana requires `@ciris.ai` Google OAuth in production.
- IP addresses are never stored. User location fields are agent-declared
  (consent-timestamped per batch) and server-coarsened to a ~55km grid
  before storage.
- PII scrubbing at ingest; scrubbed traces re-signed by CIRISLens, original
  content hash preserved for provenance without retaining PII.
- Signature verification failures are logged loudly
  (`SIGNATURE_REJECT_UNKNOWN_KEY`), never silently dropped.

## Documentation

- [CLAUDE.md](CLAUDE.md) — operations, production deployment, common tasks
- [FSD/](FSD/) — formal spec documents (trace format, scoring, coherence ratchet)
- [docs/coherence-ratchet/](docs/coherence-ratchet/) — detection mechanisms
- [sql/](sql/) — schema migrations

## Support

- Issues: https://github.com/CIRISAI/CIRISLens/issues
- Community: https://discord.gg/ciris

## License

Apache 2.0 — see [LICENSE](LICENSE).
