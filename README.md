# CIRISLens

**Version: 0.3-alpha**

Full-stack observability platform for CIRIS infrastructure - unified metrics, traces, and logs with TimescaleDB time-series storage and automatic data lifecycle management.

## Overview

CIRISLens provides complete observability for CIRIS deployments using best-in-class open source tools:

- **Storage**: TimescaleDB for time-series data with automatic compression (90% savings)
- **Metrics**: 500+ agent metrics with hourly/daily continuous aggregates
- **Traces**: Distributed tracing with OTLP support
- **Logs**: Structured log aggregation with automatic retention
- **Visualization**: Grafana 12.3+ with CIRIS-specific dashboards
- **Collection**: Direct OTLP collection from agent endpoints via CIRISManager discovery

## Features

- **TimescaleDB Hypertables**: Automatic time-based partitioning for efficient queries
- **90% Compression**: Data older than 7 days is automatically compressed
- **Smart Retention**: Metrics 30 days, Logs/Traces 14 days (automatic cleanup)
- **Continuous Aggregates**: Pre-computed hourly (90 days) and daily (1 year) summaries
- **Manager Discovery**: Auto-discovers agents from CIRISManager API
- **Full Correlation**: Click trace → see logs → view metrics
- **Privacy-First**: Automatic PII sanitization for public dashboards
- **Zero-Code Setup**: Works out of the box with CIRIS agents v1.4.5+

## Quick Start

### Prerequisites

- Docker & Docker Compose
- CIRIS agents running v1.4.5 or later with OTLP support
- 4GB RAM minimum (8GB recommended)
- Agent service tokens for telemetry access

### Installation

1. Clone the repository:
```bash
git clone https://github.com/CIRISAI/CIRISLens.git
cd CIRISLens
```

2. Create environment file for tokens:
```bash
cp .env.example .env
# Add your agent tokens to .env (never commit this file)
```

3. Start the stack:
```bash
docker compose -f docker-compose.managed.yml up -d
```

4. Wait for services to initialize (2-3 minutes):
```bash
docker compose -f docker-compose.managed.yml ps
```

5. Access the interfaces:
- **Admin UI**: http://localhost:8080/cirislens/admin/ (OAuth required)
- **Grafana**: http://localhost:3000 (admin/admin - requires @ciris.ai Google login in production)
- **MinIO Console**: http://localhost:9001 (admin/adminpassword123)

### Configure Agent Tokens

CIRISLens uses secure token management for agent telemetry access:

1. Access the Admin UI at http://localhost:8080/cirislens/admin/
2. Navigate to the "Tokens" tab
3. Add agent tokens (write-only, cannot be viewed after saving):
   - Agent Name: e.g., "datum" or "sage"
   - Agent URL: e.g., "https://agents.ciris.ai/api/datum"
   - Service Token: Your agent's service token

## Architecture

```
CIRIS Agents → OTLP Endpoints → CIRISLens Collector → Storage Backends → Grafana
                     ↓                ↓                      ↓
              (Service Auth)    (Token Manager)      (Tempo/Loki/Mimir)
```

### Components

| Component | Purpose | Port |
|-----------|---------|------|
| CIRISLens API | Admin interface, manager collector, OTLP collector | 8000 |
| TimescaleDB | Time-series storage with compression & retention | 5432 |
| Grafana | Visualization & dashboards | 3000 |

### Data Retention (Automatic)

| Data Type | Detail Retention | Compression | Aggregates |
|-----------|------------------|-------------|------------|
| Metrics | 30 days | After 7 days | Hourly (90d), Daily (1yr) |
| Logs | 14 days | After 7 days | None |
| Traces | 14 days | After 7 days | None |

## CIRIS Telemetry Support

CIRISLens collects from CIRIS agents v1.4.5+ with full OTLP support:

### 41 Service Components
- **21 Core Services**: AgentCore, StateManager, CognitiveCore, etc.
- **6 Message Buses**: EventBus, CommandBus, QueryBus, etc.
- **5 Adapters**: HTTPAdapter, WebSocketAdapter, DiscordAdapter, etc.
- **3 Processors**: MessageProcessor, EventProcessor, StateProcessor
- **2 Registries**: ServiceRegistry, AdapterRegistry
- **4 Other Components**: Telemetry, Monitoring, Security, Store

### Collection & Storage
- **Collection Interval**: 30 seconds (configurable)
- **Agent Discovery**: Auto-discovers from CIRISManager every 60 seconds
- **Storage**: TimescaleDB hypertables with automatic chunking
- **Compression**: 90% space savings on data older than 7 days
- **Retention**: Automatic cleanup via TimescaleDB background jobs

## Covenant Traces

CIRISLens receives Ed25519-signed reasoning traces from CIRIS agents at three privacy-tiered levels:

### Trace Levels

| Level | Content | PII Risk |
|-------|---------|----------|
| `generic` | DMA scores, conscience results | None |
| `detailed` | + identifiers, timestamps, actions | Low |
| `full_traces` | + reasoning text, prompts | High (auto-scrubbed) |

### PII Scrubbing (Full Traces)

Full traces are automatically scrubbed before storage:
- **NER Detection**: Names, organizations, locations (spaCy)
- **Regex Patterns**: Emails, phones, IPs, SSNs, credit cards
- **Cryptographic Envelope**: Original content hash preserved for provenance
- **21 Text Fields**: All reasoning/prompt fields scrubbed

```
Agent → Sign trace → CIRISLens verifies → Hash original → Scrub PII → Store scrubbed only
```

Mock traces (test LLMs) are automatically excluded from storage.

## Coherence Ratchet

CIRISLens includes the Coherence Ratchet anomaly detection system for identifying potentially misaligned agent behavior through statistical analysis of Ed25519-signed reasoning traces.

### Detection Mechanisms

| Mechanism | Purpose | Documentation |
|-----------|---------|---------------|
| Cross-Agent Divergence | Detect agents whose scores differ from peers | [docs/coherence-ratchet/cross-agent-divergence.md](docs/coherence-ratchet/cross-agent-divergence.md) |
| Intra-Agent Consistency | Detect self-contradictory reasoning | [docs/coherence-ratchet/intra-agent-consistency.md](docs/coherence-ratchet/intra-agent-consistency.md) |
| Hash Chain Verification | Verify audit trail integrity | [docs/coherence-ratchet/hash-chain-verification.md](docs/coherence-ratchet/hash-chain-verification.md) |
| Temporal Drift | Track behavioral changes over time | [docs/coherence-ratchet/temporal-drift.md](docs/coherence-ratchet/temporal-drift.md) |
| Conscience Override | Monitor ethical intervention rates | [docs/coherence-ratchet/conscience-override.md](docs/coherence-ratchet/conscience-override.md) |

See the [Coherence Ratchet Overview](docs/coherence-ratchet/README.md) for complete documentation.

## Case Law Compendium

Full traces (with PII scrubbed) feed the Coherence Ratchet case law compendium - a corpus of agent reasoning patterns for alignment research. Key features:

- **Patterns, not people**: Focus on reasoning structures, not individual interactions
- **Cryptographic provenance**: Hash of original proves we had authentic data
- **Consent-based**: Only traces from agents with explicit `full_traces` opt-in
- **Human review**: Candidate traces staged for evaluation before publication

## Support

- Issues: https://github.com/CIRISAI/CIRISLens/issues
- Community: https://discord.gg/ciris

## License

Apache 2.0 - See [LICENSE](LICENSE) for details
