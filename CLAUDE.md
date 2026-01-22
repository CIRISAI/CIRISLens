# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with CIRISLens.

## Project Overview

CIRISLens is the **observability layer** for the CIRIS ecosystem. It collects, stores, and visualizes telemetry data (metrics, traces, logs) from all CIRIS components using industry-standard open source tools.

## Relationship to Other CIRIS Projects

### The CIRIS Trinity

1. **CIRISAgent** (Business Logic)
   - **Role**: Executes agent logic, handles messages, manages cognitive states
   - **Telemetry**: EXPOSES metrics/traces/logs via `/v1/telemetry/unified` endpoint
   - **Version**: 1.4.3+ has full OpenTelemetry support with 500+ metrics

2. **CIRISManager** (Lifecycle Management)
   - **Role**: Manages agent containers, handles deployments, routes traffic
   - **Telemetry**: EXPOSES its own operational metrics
   - **Note**: NO LONGER collects or stores telemetry (that's CIRISLens's job)

3. **CIRISLens** (Observability)
   - **Role**: Collects all telemetry, provides dashboards, enables debugging
   - **Telemetry**: COLLECTS from agents and manager, STORES in time-series DBs
   - **This Project**: You are here!

### Data Flow

```
CIRISAgent → Exposes telemetry → CIRISLens collects → Grafana visualizes
CIRISManager → Exposes metrics → CIRISLens collects → Grafana visualizes
```

## Architecture Principles

### Use Best-in-Class Tools
- **DO**: Use Grafana Labs stack (Grafana, Loki, Tempo, Mimir)
- **DO**: Use OpenTelemetry for collection
- **DON'T**: Write custom collectors or storage backends
- **DON'T**: Reinvent what Prometheus/Grafana already does well

### Configuration Over Code
- **DO**: Configure tools via YAML
- **DO**: Use Grafana provisioning for dashboards
- **DON'T**: Write custom processing code unless absolutely necessary
- **DON'T**: Build custom UIs - use Grafana

### Privacy by Design
- **DO**: Require authentication for all dashboards (@ciris.ai Google OAuth)
- **DO**: Sanitize PII in OpenTelemetry Collector
- **DO**: Hash agent IDs in any future public-facing views
- **DON'T**: Store message content or user data
- **DON'T**: Expose internal IPs or secrets

## CIRIS Telemetry Taxonomy

CIRISAgent 1.4.3+ exposes telemetry organized by component:

### 45 Metric Sources
- **21 Core Services**: AgentCore, StateManager, CognitiveCore, etc.
- **6 Message Buses**: EventBus, CommandBus, QueryBus, etc.
- **5 Adapters**: HTTPAdapter, WebSocketAdapter, DiscordAdapter, etc.
- **3 Processors**: MessageProcessor, EventProcessor, StateProcessor
- **2 Registries**: ServiceRegistry, AdapterRegistry
- **8 Other Components**: Telemetry, Monitoring, Security, etc.

### Key Metrics to Track
```yaml
# Cognitive State (most important)
ciris_agent_cognitive_state{state="WORK|DREAM|PLAY|SOLITUDE"}

# Resource Usage
ciris_agent_llm_tokens_total{model="gpt-4"}
ciris_agent_llm_cost_cents_total{}

# Message Flow
ciris_messagebus_messages_total{bus="event|command|query"}
ciris_messagebus_latency_seconds{}

# Adapter Activity
ciris_adapter_requests_total{adapter="http|websocket|discord"}
ciris_adapter_active_connections{}
```

## Production Deployment

### Access Production CIRISLens

```bash
# SSH to production server (same server as CIRISManager)
ssh -i ~/.ssh/ciris_deploy root@agents.ciris.ai

# CIRISLens location
cd /opt/cirislens

# View running services
docker-compose ps

# Check logs
docker-compose logs -f api         # Manager collector + OTLP collector
docker-compose logs -f grafana     # Grafana dashboard
docker-compose logs -f postgres    # TimescaleDB

# Restart services
docker-compose restart
```

### Production URLs
- **Grafana**: https://agents.ciris.ai/lens/ (requires @ciris.ai Google login)
- **Admin UI**: https://agents.ciris.ai/lens/admin/ (OAuth required)
- **API Health**: https://agents.ciris.ai/lens/api/health
- **Internal API**: localhost:8000

### Production Stack

| Component | Image | Purpose |
|-----------|-------|---------|
| TimescaleDB | `timescale/timescaledb:latest-pg15` | Time-series storage with compression |
| Grafana | `grafana/grafana:latest` | Visualization (currently 12.3.0) |
| CIRISLens API | `cirislens-api:dev` | Manager collector + OTLP collector |

### TimescaleDB Configuration

The production database uses TimescaleDB with automatic data lifecycle management:

```sql
-- View hypertables
SELECT * FROM timescaledb_information.hypertables;

-- View background jobs (compression, retention, aggregates)
SELECT job_id, proc_name, schedule_interval, next_start
FROM timescaledb_information.jobs;

-- View compression status
SELECT hypertable_name,
       pg_size_pretty(before_compression_total_bytes) as before,
       pg_size_pretty(after_compression_total_bytes) as after
FROM timescaledb_information.compression_settings;

-- Manual compression (if needed)
SELECT compress_chunk(c) FROM show_chunks('cirislens.agent_metrics') c;
```

### Data Retention Policies (Automatic)

| Table | Detail Retention | Compression | Continuous Aggregates |
|-------|------------------|-------------|----------------------|
| agent_metrics | 30 days | After 7 days | Hourly (90d), Daily (1yr) |
| agent_logs | 14 days | After 7 days | None |
| agent_traces | 14 days | After 7 days | None |

### Block Storage

Production data is stored on a dedicated 100GB block volume:
```bash
# Check disk usage
df -h /mnt/lens_volume

# Data locations (bind mounts in docker-compose.yml)
/mnt/lens_volume/data/postgres  # TimescaleDB data
/mnt/lens_volume/data/grafana   # Grafana data
```

### Connecting Agents to Production CIRISLens

Agents send telemetry to CIRISLens via environment variables:

```yaml
# In agent docker-compose.yml
environment:
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://observability.ciris.ai:4317"
  OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"
  OTEL_SERVICE_NAME: "${AGENT_NAME}"
  OTEL_RESOURCE_ATTRIBUTES: "agent.id=${AGENT_ID},agent.template=${TEMPLATE}"
```

## Development Workflow

### Local Testing

```bash
# Start local stack
docker-compose up -d

# Generate test data
curl -X POST http://localhost:4318/v1/traces \
  -H "Content-Type: application/json" \
  -d @test/sample-trace.json

# Access Grafana
open http://localhost:3000
```

### Adding New Dashboards

1. Create dashboard in Grafana UI
2. Export as JSON: Settings → JSON Model
3. Save to `dashboards/` directory
4. Commit to git

### Modifying Collector Config

1. Edit `config/otel-collector.yaml`
2. Restart collector: `docker-compose restart otel-collector`
3. Check logs: `docker-compose logs -f otel-collector`

## Common Tasks

### View Agent Metrics
```promql
# In Grafana, query Mimir datasource
ciris_agent_cognitive_state{agent_id=~".*"}
```

### Find Slow Traces
```
# In Grafana, query Tempo datasource
{duration > 1000ms && service.name = "CIRISAgent"}
```

### Search Logs
```logql
# In Grafana, query Loki datasource
{service="CIRISAgent"} |= "error"
```

### Correlate Everything
1. Find interesting trace in Tempo
2. Click "Logs for this span" → Shows related logs
3. Click "Metrics" → Shows metrics during trace timespan

## Troubleshooting

### No Data Showing

1. Check agent is exposing metrics:
```bash
curl http://agent:8080/v1/telemetry/unified?format=prometheus
```

2. Check collector is receiving:
```bash
docker-compose logs otel-collector | grep "datapoints"
```

3. Check Prometheus targets:
```
http://localhost:9090/targets
```

### High Memory Usage

Adjust in `docker-compose.yml`:
```yaml
services:
  mimir:
    deploy:
      resources:
        limits:
          memory: 2G  # Increase as needed
```

### Disk Space

TimescaleDB handles retention automatically, but if disk fills up:

```bash
# Check disk usage
df -h /mnt/lens_volume

# Check table sizes
docker exec cirislens-db psql -U cirislens -d cirislens -c "
SELECT tablename, pg_size_pretty(pg_total_relation_size('cirislens.' || tablename)) as size
FROM pg_tables WHERE schemaname = 'cirislens' ORDER BY pg_total_relation_size('cirislens.' || tablename) DESC;
"

# Manual cleanup (if retention jobs haven't run)
docker exec cirislens-db psql -U cirislens -d cirislens -c "
DELETE FROM cirislens.agent_metrics WHERE timestamp < NOW() - INTERVAL '30 days';
VACUUM FULL cirislens.agent_metrics;
"

# Force compression on old chunks
docker exec cirislens-db psql -U cirislens -d cirislens -c "
SELECT compress_chunk(c) FROM show_chunks('cirislens.agent_metrics', older_than => INTERVAL '7 days') c WHERE NOT is_compressed;
"
```

## Important Notes

### What CIRISLens Does NOT Do
- **Does NOT** modify agent behavior
- **Does NOT** store message content
- **Does NOT** make decisions (that's CIRISManager's job)
- **Does NOT** require code changes to agents

### What CIRISLens DOES
- **DOES** collect all telemetry data
- **DOES** provide unified visualization
- **DOES** enable debugging and troubleshooting
- **DOES** track costs and resource usage
- **DOES** maintain privacy boundaries

## Best Practices

1. **Keep dashboards focused**: One dashboard per concern
2. **Use variables**: Make dashboards reusable with Grafana variables
3. **Set up alerts**: Use Grafana alerting for critical metrics
4. **Document queries**: Add descriptions to dashboard panels
5. **Version control**: All dashboards in git

## Security Considerations

- **Authentication required**: Grafana uses Google OAuth restricted to @ciris.ai domain
- **Never expose raw Prometheus/Loki/Tempo ports publicly**
- **Always use Grafana as the gateway**
- **Sanitize data in collector, not dashboards**
- **Use read-only datasources where possible**

## CIRIS Covenant 1.0b Compliance Infrastructure

CIRISLens provides the observability infrastructure required by the CIRIS Covenant for transparency, accountability, and audit trails.

### Covenant API Endpoints

```
POST   /api/v1/covenant/wbd/deferrals          # Record WBD event
GET    /api/v1/covenant/wbd/deferrals          # List WBD events
PUT    /api/v1/covenant/wbd/deferrals/{id}/resolve

POST   /api/v1/covenant/pdma/events            # Record PDMA decision
GET    /api/v1/covenant/pdma/events
PUT    /api/v1/covenant/pdma/events/{id}/outcomes

POST   /api/v1/covenant/creator-ledger         # Log creation (tamper-evident)
GET    /api/v1/covenant/creator-ledger

POST   /api/v1/covenant/sunset-ledger          # Initiate decommissioning
GET    /api/v1/covenant/sunset-ledger
PUT    /api/v1/covenant/sunset-ledger/{id}/progress

GET    /api/v1/covenant/compliance/status      # Agent compliance status
GET    /api/v1/covenant/compliance/summary     # Aggregate compliance
```

### Covenant Database Tables

| Table | Purpose | Covenant Reference |
|-------|---------|-------------------|
| `wbd_deferrals` | Wisdom-Based Deferral tracking | Section II, Ch 3 |
| `pdma_events` | PDMA decision rationale chains | Section II, Ch 2 |
| `creator_ledger` | Tamper-evident creation accountability | Section VI, Ch 3 |
| `sunset_ledger` | Decommissioning protocol tracking | Section VIII, Ch 4 |

### Agent Covenant Fields

The `cirislens.agents` table includes:
- `sentience_probability` (0.0-1.0)
- `autonomy_level` (1-5)
- `stewardship_tier` (1-5)
- `covenant_version` (e.g., "1.0b")
- `pdma_enabled`, `wbd_enabled` (boolean)
- `total_pdma_events`, `total_wbd_deferrals` (counters)

### Migration

Run `sql/010_covenant_compliance.sql` to create Covenant tables.

## CIRISProxy Integration

CIRISProxy sends `llm_error` events with these fields (stored in `service_logs.attributes`):

| Field | Example |
|-------|---------|
| `provider` | `"groq"`, `"together"`, `"openrouter"`, `"openai"` |
| `actual_model` | `"openrouter/meta-llama/llama-4-maverick"` |
| `api_base` | `"https://..."` |

Debug query for LLM errors by provider:
```sql
SELECT provider, COUNT(*) as errors, MAX(error) as last_error
FROM cirislens.service_logs
WHERE event = 'llm_error' AND timestamp > NOW() - INTERVAL '1 hour'
GROUP BY provider ORDER BY errors DESC;
```

## Coherence Ratchet Detection System

The Coherence Ratchet is CIRISLens's anomaly detection system for identifying potentially misaligned agent behavior through statistical analysis of reasoning traces.

### What It Does

Analyzes Ed25519-signed reasoning traces from CIRIS agents to detect:
- **Cross-agent divergence**: Agents whose DMA scores differ significantly from peers in the same domain
- **Intra-agent inconsistency**: Agents contradicting their own prior reasoning patterns
- **Hash chain breaks**: Gaps or tampering in the audit trail
- **Temporal drift**: Sudden changes in agent behavior over time
- **Conscience override patterns**: Elevated rates of ethical faculty interventions

**Important**: Detection is triage, not verdict. Anomalies warrant human investigation but don't prove misalignment.

### API Endpoints

```
GET    /api/v1/covenant/coherence-ratchet/alerts     # List anomaly alerts
POST   /api/v1/covenant/coherence-ratchet/run        # Trigger detection manually
PUT    /api/v1/covenant/coherence-ratchet/alerts/{id}/acknowledge
PUT    /api/v1/covenant/coherence-ratchet/alerts/{id}/resolve
GET    /api/v1/covenant/coherence-ratchet/stats      # Detection statistics
```

### Detection Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Cross-agent divergence (z-score) | > 2σ | > 3σ |
| Daily score drift | > 15% | > 25% |
| Conscience override rate | > 2x domain avg | > 3x domain avg |
| Hash chain gaps | - | Any gap |

### Database Tables

| Table | Purpose |
|-------|---------|
| `covenant_traces` | Stores signed reasoning traces with denormalized DMA scores |
| `coherence_ratchet_alerts` | Persisted anomaly alerts with lifecycle tracking |
| `covenant_public_keys` | Ed25519 public keys for signature verification |
| `lens_signing_keys` | CIRISLens signing keys for PII scrubbing operations |
| `case_law_candidates` | Staging table for traces evaluated for case law compendium |

### Grafana Dashboard

The "Coherence Ratchet Detection" dashboard (`dashboards/coherence_ratchet.json`) provides:
- Alert overview (warning/critical counts, hash chain breaks)
- Cross-agent divergence time series with threshold lines
- Temporal drift analysis with significant change highlighting
- Conscience override rates by agent/domain
- Recent anomaly alerts table

### Running Detection Manually

```bash
# Via API
curl -X POST https://agents.ciris.ai/lens/api/v1/covenant/coherence-ratchet/run

# The scheduler runs automatically:
# - Cross-agent divergence: daily
# - Temporal drift: daily
# - Hash chain verification: hourly
# - Conscience overrides: daily
# - Intra-agent consistency: daily
```

### Migration

Run `sql/011_covenant_traces.sql` to create Coherence Ratchet tables.

### FSD Documentation

- [Trace Format Specification](FSD/trace_format_specification.md) - Canonical trace structure
- [Coherence Ratchet Detection](FSD/coherence_ratchet_detection.md) - Detection mechanisms
- [CIRIS Scoring Specification](FSD/ciris_scoring_specification.md) - Scoring methodology

## Covenant Trace Levels

Agents can emit traces at three privacy-tiered levels, controlled by opt-in consent:

### Trace Level Summary

| Level | Content | Use Case | PII Risk |
|-------|---------|----------|----------|
| `generic` | Scores only (DMA, conscience) | Aggregate statistics | None |
| `detailed` | + identifiers, timestamps, action types | Debugging, audits | Low |
| `full_traces` | + reasoning text, prompts, context | Case law corpus | High (scrubbed) |

### Level Details

**`generic`** (Default)
- CSDMA plausibility score, DSDMA domain alignment
- PDMA stakeholder/conflict indicators (no details)
- Conscience pass/fail, override status
- IDMA k_eff and fragility flag
- Safe for public dashboards

**`detailed`** (Opt-in)
- Everything in `generic` plus:
- Agent name, thought ID, task ID
- Timestamps (started_at, completed_at)
- Selected action type, success status
- Cognitive state, thought depth
- Token usage, cost, models used

**`full_traces`** (Explicit consent for Coherence Ratchet corpus)
- Everything in `detailed` plus:
- Full reasoning text from all DMAs
- Prompts used, action rationale
- Context snapshots, conversation history
- Conscience override reasons
- **Requires PII scrubbing before storage**

### Trace Submission Endpoint

```
POST /api/v1/covenant/traces
Content-Type: application/json

{
  "events": [...],
  "trace_level": "full_traces",
  "batch_timestamp": "2026-01-22T15:00:00Z",
  "consent_timestamp": "2026-01-22T14:00:00Z"
}
```

## PII Scrubbing for Full Traces

Full traces contain reasoning text that may include PII. CIRISLens automatically scrubs PII while preserving cryptographic provenance.

### Scrubbing Pipeline

```
Agent sends full_trace → Verify agent signature → Hash original content
    → Scrub PII (NER + regex) → Sign scrubbed version → Store only scrubbed
```

### What Gets Scrubbed

**21 Text Fields** (from trace components):
- `task_description`, `initial_context`
- `system_snapshot`, `gathered_context`, `relevant_memories`, `conversation_history`
- `reasoning`, `prompt_used`, `combined_analysis`
- `action_rationale`, `reasoning_summary`, `action_parameters`, `aspdma_prompt`
- `conscience_override_reason`, `epistemic_data`, `updated_status_content`
- `entropy_reason`, `coherence_reason`, `optimization_veto_justification`
- `epistemic_humility_justification`, `execution_error`

### Entity Detection

**NER (spaCy `en_core_web_sm`):**
- `PERSON` → `[PERSON_1]`, `[PERSON_2]`, etc.
- `ORG` → `[ORG_1]`, `[ORG_2]`, etc.
- `GPE`, `FAC`, `LOC`, `NORP` (geopolitical, facilities, locations)

**Regex Patterns:**
- Email → `[EMAIL]`
- Phone → `[PHONE]`
- IP Address → `[IP_ADDRESS]`
- URL → `[URL]`
- SSN → `[SSN]`
- Credit Card → `[CREDIT_CARD]`

### Cryptographic Envelope

Preserves provenance while allowing PII deletion:

| Field | Purpose | Depends on scrub key? |
|-------|---------|----------------------|
| `original_content_hash` | SHA-256 of pre-scrub content | ❌ No |
| `signature` | Agent's Ed25519 signature (verified) | ❌ No |
| `signature_verified` | Whether agent signature was valid | ❌ No |
| `scrub_timestamp` | When scrubbing occurred | ❌ No |
| `scrub_signature` | CIRISLens signature of scrubbed content | ✅ Yes |
| `scrub_key_id` | Identifier of CIRISLens signing key | ✅ Yes |
| `pii_scrubbed` | Boolean flag | ❌ No |

**Key point:** If the scrub signing key is lost, provenance is still provable via `original_content_hash`. The scrub key only provides tamper-evidence for the scrubbed version.

### Scrub Key Management

```bash
# Generate new scrub signing keypair
python scripts/generate_scrub_key.py --output-dir /opt/ciris/lens/keys

# Configure (add to environment)
export CIRISLENS_SCRUB_KEY_PATH=/opt/ciris/lens/keys/lens_scrub_private.key

# Register public key in database (run generated SQL)
psql -f keys/register_scrub_key.sql
```

### Migration

Run `sql/012_pii_scrubbing.sql` to add envelope columns and create `lens_signing_keys` table.

## Mock Trace Filtering

Traces from test/mock LLMs are automatically excluded from storage to keep the production corpus clean.

### Detection

Any trace where `models_used` contains "mock" (case-insensitive) is skipped:
- `llama4scout (mock)` → excluded
- `mock-model` → excluded
- `meta-llama/Llama-4-Maverick-17B` → stored

### Logging

Mock traces are logged but not stored:
```
DEBUG: Skipping mock trace trace-123 (models: ["llama4scout (mock)"])
```

## Test Coverage

- **516 tests** passing
- **75% coverage** (target: 70%)
- Run tests: `pytest tests/ -x -q`
- Run with coverage: `pytest tests/ --cov=api --cov=sdk`

### Test Breakdown

| Module | Tests | Coverage |
|--------|-------|----------|
| Coherence Ratchet | 49 | Detection algorithms |
| Covenant API | 38 | Trace ingestion, IDMA, metadata |
| PII Scrubber | 64 | NER, regex, envelope, signing |
| Status/Health | 28 | Service status collection |
| Log Ingest | 28 | Log sanitization, storage |
| Manager Collector | 29 | Agent discovery, metrics |
| OTLP Collector | 48 | Trace/metric collection |
| Other | 232 | API routes, resilience, etc. |

## CI/CD Pipeline

### Build Workflow (`.github/workflows/build.yml`)
- Runs on push to main and PRs
- Executes tests with coverage
- Performs SonarCloud analysis

### Docker Publish (`.github/workflows/docker-publish.yml`)
- Builds multi-arch images (amd64/arm64)
- Publishes to `ghcr.io/cirisai/cirislens`
- Tags: `latest`, `v*` versions, commit SHA

### Building Locally

```bash
# Build production image
docker build -t cirislens-api:dev -f api/Dockerfile --target production .

# Build with development tools
docker build -t cirislens-api:dev-tools -f api/Dockerfile --target development .

# Run locally
docker run -p 8000:8000 cirislens-api:dev
```

## Future Enhancements

Potential additions (not yet implemented):
- Grafana OnCall for incident management
- Grafana k6 for load testing
- Grafana Faro for frontend monitoring
- Custom Grafana plugin for CIRIS topology visualization
- Phase 2 Coherence Ratchet: Semantic embedding analysis, rationale-outcome correlation