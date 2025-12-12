# CIRISLens Unified Logging Platform Plan

**Created**: 2025-12-10
**Updated**: 2025-12-11
**Status**: In Progress (Phase 1 Complete)
**Author**: Claude Code (for Eric Moore)

## Executive Summary

Extend CIRISLens to become the centralized log aggregation and observability platform for all CIRIS services (Billing, Proxy, Manager, Agents). This document covers the technical implementation, data isolation strategy, and public vs private access considerations.

---

## Current State

### CIRISLens Infrastructure
- **Database**: TimescaleDB with automatic compression (90% savings) and retention
- **Storage**: 100GB block volume at `/mnt/lens_volume`
- **Visualization**: Grafana at `https://agents.ciris.ai/lens/`
- **Current Data**: Agent metrics, traces, logs from CIRIS agents

### Current Tables
| Table | Purpose | Retention |
|-------|---------|-----------|
| `agent_metrics` | Agent telemetry metrics | 30 days detail, 90d hourly, 1yr daily |
| `agent_traces` | Distributed traces | 14 days |
| `agent_logs` | Agent application logs | 14 days |
| `discovered_agents` | Agent registry | Indefinite |

### Dashboard Access Status (Updated 2025-12-11)
- **PRIVATE**: `/lens/` now requires @ciris.ai Google OAuth login
- Shows agent health, cognitive states, resource usage
- Safe to add service logs - only authenticated CIRIS team members can view

---

## Proposed Architecture

### New Service Logs Table

```sql
-- Service logs from Billing, Proxy, Manager
CREATE TABLE IF NOT EXISTS service_logs (
    id BIGSERIAL,
    service_name VARCHAR(100) NOT NULL,      -- ciris-billing-api, ciris-proxy, ciris-manager
    server_id VARCHAR(50),                    -- main, scout, billing
    timestamp TIMESTAMPTZ NOT NULL,
    level VARCHAR(20) NOT NULL,               -- DEBUG, INFO, WARNING, ERROR, CRITICAL
    event VARCHAR(255),                       -- structured event name
    logger VARCHAR(255),                      -- source logger name
    message TEXT,
    request_id VARCHAR(64),                   -- HTTP request correlation
    trace_id VARCHAR(64),                     -- distributed trace ID
    user_hash VARCHAR(16),                    -- hashed user identifier (privacy)
    attributes JSONB DEFAULT '{}'::jsonb,
    PRIMARY KEY (timestamp, id)
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('service_logs', 'timestamp', if_not_exists => TRUE);

-- Indexes
CREATE INDEX idx_service_logs_service ON service_logs(service_name, timestamp DESC);
CREATE INDEX idx_service_logs_level ON service_logs(level, timestamp DESC) WHERE level IN ('ERROR', 'CRITICAL', 'WARNING');
CREATE INDEX idx_service_logs_event ON service_logs(event, timestamp DESC);
CREATE INDEX idx_service_logs_request ON service_logs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX idx_service_logs_trace ON service_logs(trace_id) WHERE trace_id IS NOT NULL;
CREATE INDEX idx_service_logs_attrs ON service_logs USING gin(attributes);

-- Compression policy (after 7 days)
SELECT add_compression_policy('service_logs', INTERVAL '7 days');

-- Retention policy (keep 30 days of detail)
SELECT add_retention_policy('service_logs', INTERVAL '30 days');
```

### Log Categories

| Category | Event Patterns | Sensitivity | Public? |
|----------|---------------|-------------|---------|
| **access** | `request_started`, `request_completed` | Medium | No - contains IPs |
| **billing** | `charge_created`, `purchase_verified`, `credits_added` | High | No - financial data |
| **errors** | `*_error`, `*_failed` | Medium | Aggregates only |
| **security** | `token_expired`, `auth_failed`, `rate_limited` | High | No |
| **usage** | `llm_usage_logged` | Medium | Aggregates only |

---

## Data Isolation Strategy

### Option 1: Separate Databases (Recommended)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CIRISLens Infrastructure                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────┐      ┌─────────────────────┐          │
│  │  PUBLIC DATABASE    │      │  PRIVATE DATABASE   │          │
│  │  cirislens_public   │      │  cirislens_private  │          │
│  ├─────────────────────┤      ├─────────────────────┤          │
│  │ - agent_metrics     │      │ - service_logs      │          │
│  │ - agent_health_agg  │      │ - billing_events    │          │
│  │ - public_dashboards │      │ - security_events   │          │
│  │                     │      │ - raw_access_logs   │          │
│  │ Retention: 7 days   │      │ Retention: 90 days  │          │
│  └─────────────────────┘      └─────────────────────┘          │
│           │                            │                        │
│           ▼                            ▼                        │
│  ┌─────────────────────┐      ┌─────────────────────┐          │
│  │  PUBLIC GRAFANA     │      │  PRIVATE GRAFANA    │          │
│  │  /lens/ (anonymous) │      │  /lens-admin/ (auth)│          │
│  │                     │      │                     │          │
│  │  - Agent overview   │      │  - All dashboards   │          │
│  │  - Public metrics   │      │  - Service logs     │          │
│  │  - Cognitive states │      │  - Billing analytics│          │
│  └─────────────────────┘      │  - Security alerts  │          │
│                               └─────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

**Pros:**
- Complete data isolation at database level
- Can't accidentally expose private data via misconfigured dashboard
- Different retention policies per database
- Can scale independently

**Cons:**
- More infrastructure to manage
- Cross-database correlation requires extra work
- Two Grafana instances to maintain

### Option 2: Single Database with Row-Level Security

```sql
-- Add visibility column to all tables
ALTER TABLE service_logs ADD COLUMN is_public BOOLEAN DEFAULT FALSE;

-- Create views for public access
CREATE VIEW public_service_logs AS
SELECT service_name, timestamp, level, event,
       CASE WHEN level IN ('ERROR', 'CRITICAL') THEN 'Error occurred' ELSE message END as message
FROM service_logs
WHERE is_public = TRUE;

-- Grafana public user only has access to public views
GRANT SELECT ON public_service_logs TO grafana_public;
```

**Pros:**
- Single database, simpler management
- Easy cross-correlation
- Less infrastructure

**Cons:**
- Risk of data leakage via SQL injection or misconfiguration
- More complex permission management
- Single point of failure

### Option 3: Take /lens/ Private for Now (Simplest)

```nginx
# Remove anonymous access, require auth for all /lens/ routes
location /lens/ {
    auth_request /auth/verify;  # Or basic auth
    proxy_pass http://cirislens-grafana:3000/;
}
```

**Pros:**
- Immediate, zero development work
- Can add public dashboards later when needed
- Focus on functionality first, publicity later

**Cons:**
- Loses public visibility (was this valuable?)
- Need to manage user access

---

## Recommendation: Option 3 Now, Option 1 Later

### Phase 1: Make /lens/ Private (Immediate)
1. Add authentication to `/lens/` route in nginx
2. Use Grafana's built-in auth (Google OAuth or basic auth)
3. All dashboards become internal-only
4. Add service log ingestion without worrying about exposure

### Phase 2: Add Service Log Ingestion (1-2 days)
1. Create `service_logs` table
2. Add `/api/v1/logs/ingest` endpoint
3. Update CIRISBilling, CIRISProxy, CIRISManager to ship logs
4. Create internal dashboards

### Phase 3: Public Dashboard Separation (Future, if needed)
1. Create separate `cirislens_public` database
2. ETL job to copy sanitized aggregates
3. Separate Grafana instance for public access
4. Only expose safe, aggregated metrics

---

## Log Ingestion API Specification

### Endpoint

```
POST /api/v1/logs/ingest
Authorization: Bearer {SERVICE_TOKEN}
Content-Type: application/x-ndjson
```

### Request Body (NDJSON - newline-delimited JSON)

```json
{"timestamp":"2025-12-10T12:00:00.123Z","service":"ciris-billing-api","server_id":"billing","level":"INFO","event":"charge_created","logger":"app.services.billing","request_id":"req_abc123","attributes":{"account_hash":"f1ce5c33","amount":5}}
{"timestamp":"2025-12-10T12:00:01.456Z","service":"ciris-billing-api","server_id":"billing","level":"ERROR","event":"payment_failed","logger":"app.services.stripe","request_id":"req_def456","message":"Card declined","attributes":{"error_code":"card_declined"}}
```

### Response

```json
{
  "status": "ok",
  "accepted": 2,
  "rejected": 0,
  "errors": []
}
```

### Authentication

Each service gets a unique token stored in CIRISLens:

```sql
CREATE TABLE service_tokens (
    service_name VARCHAR(100) PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL,  -- SHA-256 of token
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used TIMESTAMPTZ
);
```

---

## Service Integration

### CIRISBilling Changes

Update `app/observability/logging.py`:

```python
import asyncio
import httpx
from collections import deque
from datetime import datetime, timezone

class LogShipper:
    """Ships logs to CIRISLens in batches"""

    def __init__(self, endpoint: str, token: str, batch_size: int = 100, flush_interval: float = 10.0):
        self.endpoint = endpoint
        self.token = token
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.buffer = deque(maxlen=10000)  # Circuit breaker
        self._client = None
        self._flush_task = None

    async def start(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self):
        if self._flush_task:
            self._flush_task.cancel()
        await self._flush()  # Final flush
        if self._client:
            await self._client.aclose()

    def log(self, record: dict):
        """Add log record to buffer (non-blocking)"""
        record['timestamp'] = datetime.now(timezone.utc).isoformat()
        self.buffer.append(record)

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(self.flush_interval)
            await self._flush()

    async def _flush(self):
        if not self.buffer:
            return

        batch = []
        while self.buffer and len(batch) < self.batch_size:
            batch.append(self.buffer.popleft())

        if not batch:
            return

        body = '\n'.join(json.dumps(r) for r in batch)
        try:
            resp = await self._client.post(
                self.endpoint,
                content=body,
                headers={
                    'Authorization': f'Bearer {self.token}',
                    'Content-Type': 'application/x-ndjson'
                }
            )
            resp.raise_for_status()
        except Exception as e:
            # Re-queue failed logs (with limit to prevent memory leak)
            for r in reversed(batch[:100]):
                self.buffer.appendleft(r)
            logger.warning(f"Failed to ship logs: {e}")
```

### Environment Variables

```bash
# CIRISBilling
LOG_SHIP_ENDPOINT=https://agents.ciris.ai/lens/api/v1/logs/ingest
LOG_SHIP_TOKEN=billing_service_token_here
LOG_SHIP_ENABLED=true

# CIRISProxy
LOG_SHIP_ENDPOINT=https://agents.ciris.ai/lens/api/v1/logs/ingest
LOG_SHIP_TOKEN=proxy_service_token_here
LOG_SHIP_ENABLED=true

# CIRISManager
LOG_SHIP_ENDPOINT=https://agents.ciris.ai/lens/api/v1/logs/ingest
LOG_SHIP_TOKEN=manager_service_token_here
LOG_SHIP_ENABLED=true
```

---

## Grafana Dashboards

### Service Logs Dashboard

```json
{
  "title": "Service Logs",
  "panels": [
    {
      "title": "Error Rate by Service",
      "type": "timeseries",
      "query": "SELECT time_bucket('1 minute', timestamp) as time, service_name, count(*) FROM service_logs WHERE level IN ('ERROR', 'CRITICAL') GROUP BY 1, 2"
    },
    {
      "title": "Recent Errors",
      "type": "table",
      "query": "SELECT timestamp, service_name, event, message FROM service_logs WHERE level IN ('ERROR', 'CRITICAL') ORDER BY timestamp DESC LIMIT 100"
    },
    {
      "title": "Request Latency (from access logs)",
      "type": "timeseries",
      "query": "SELECT time_bucket('1 minute', timestamp) as time, service_name, avg((attributes->>'duration_seconds')::float) FROM service_logs WHERE event = 'request_completed' GROUP BY 1, 2"
    }
  ]
}
```

### Billing Analytics Dashboard

```json
{
  "title": "Billing Analytics",
  "panels": [
    {
      "title": "Charges per Hour",
      "query": "SELECT time_bucket('1 hour', timestamp), count(*) FROM service_logs WHERE event = 'charge_created' GROUP BY 1"
    },
    {
      "title": "Revenue by Day",
      "query": "SELECT date_trunc('day', timestamp), sum((attributes->>'amount_cents')::int) FROM service_logs WHERE event = 'charge_created' GROUP BY 1"
    },
    {
      "title": "Failed Payments",
      "query": "SELECT timestamp, attributes->>'error' FROM service_logs WHERE event = 'payment_failed' ORDER BY timestamp DESC"
    }
  ]
}
```

### Cross-Service Correlation Dashboard

```json
{
  "title": "Request Flow",
  "panels": [
    {
      "title": "Request Trace",
      "type": "traces",
      "description": "Follow a request_id across Billing -> Proxy -> Agent"
    },
    {
      "title": "Error Correlation",
      "description": "When billing fails, was proxy healthy? Was agent responding?"
    }
  ]
}
```

---

## Security Considerations

### Data Sanitization Rules

Before storing logs, sanitize:

```python
REDACT_PATTERNS = [
    (r'Bearer [A-Za-z0-9\-_]+', 'Bearer [REDACTED]'),
    (r'token=[A-Za-z0-9\-_]+', 'token=[REDACTED]'),
    (r'password=\S+', 'password=[REDACTED]'),
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),
    (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[CARD]'),
]

def sanitize_log(record: dict) -> dict:
    """Remove PII and secrets from log records"""
    if 'message' in record:
        for pattern, replacement in REDACT_PATTERNS:
            record['message'] = re.sub(pattern, replacement, record['message'])

    # Hash user identifiers
    if 'user_id' in record.get('attributes', {}):
        record['attributes']['user_hash'] = hashlib.sha256(
            record['attributes'].pop('user_id').encode()
        ).hexdigest()[:16]

    return record
```

### Access Control

| Role | Access |
|------|--------|
| `viewer` | Public dashboards only (when enabled) |
| `analyst` | All dashboards, read-only |
| `admin` | All dashboards + Grafana admin |
| `service` | Log ingestion API only |

---

## Implementation Checklist

### Phase 1: Make /lens/ Private ✅ COMPLETE (2025-12-11)
- [x] ~~Update nginx config to require auth for `/lens/`~~ (Not needed - Grafana handles auth internally)
- [x] Configure Grafana Google OAuth (reuse existing CIRIS OAuth)
- [x] Test access with @ciris.ai accounts
- [x] Remove `GF_AUTH_ANONYMOUS_ENABLED=true` from docker-compose

**Implementation notes:**
- Used Grafana's built-in Google OAuth instead of nginx auth_request
- Added `https://agents.ciris.ai/lens/login/google` to Google Cloud Console redirect URIs
- Restricted to `@ciris.ai` domain via `GF_AUTH_GOOGLE_ALLOWED_DOMAINS`

### Phase 2: Add Log Ingestion
- [ ] Create `service_logs` table migration
- [ ] Create `service_tokens` table
- [ ] Add `/api/v1/logs/ingest` endpoint to CIRISLens API
- [ ] Generate service tokens for Billing, Proxy, Manager
- [ ] Test ingestion with curl

### Phase 3: Integrate Services
- [ ] Add LogShipper to CIRISBilling
- [ ] Add LogShipper to CIRISProxy
- [ ] Add LogShipper to CIRISManager
- [ ] Verify logs appearing in CIRISLens

### Phase 4: Create Dashboards
- [ ] Service Logs overview dashboard
- [ ] Billing analytics dashboard
- [ ] Error correlation dashboard
- [ ] Security events dashboard

### Phase 5: Alerting (Optional)
- [ ] Configure Grafana alerting
- [ ] Error rate alerts
- [ ] Payment failure alerts
- [ ] Security incident alerts

---

## Rollback Plan

If issues arise:

1. **Log ingestion failures**: Services fall back to local file logging
2. **Database issues**: TimescaleDB has point-in-time recovery
3. **Grafana issues**: Dashboards are version controlled in git
4. **Need public access back**: Re-enable anonymous auth in Grafana

---

## Cost Estimate

### Storage (TimescaleDB with compression)

| Service | Events/day | Raw Size | Compressed | Monthly |
|---------|-----------|----------|------------|---------|
| Billing | ~10,000 | 10 MB | 1 MB | 30 MB |
| Proxy | ~100,000 | 100 MB | 10 MB | 300 MB |
| Manager | ~5,000 | 5 MB | 0.5 MB | 15 MB |
| Agents | ~50,000 | 50 MB | 5 MB | 150 MB |
| **Total** | ~165,000 | 165 MB | 16.5 MB | **~500 MB** |

Current 100GB volume is more than sufficient.

### Compute

No additional containers needed - existing CIRISLens API handles ingestion.

---

## Regulatory Compliance Research (2025-12-11)

Research conducted on AI regulatory requirements for CIRIS Android app billing/proxy usage data.

### EU Regulations

**GDPR (General Data Protection Regulation)**
- **Storage Limitation Principle**: Delete personal data when no longer needed for original purpose
- No specific retention period mandated - must be "no longer than necessary"
- Must document and justify retention periods in privacy policy

**EU AI Act (2024)**
- **10-year retention** required for technical documentation and logs for high-risk AI systems
- This applies to system logs, NOT personal data (GDPR still requires minimization)
- Logs must include: input data characteristics, training decisions, system modifications
- **Effective**: August 2025 for most provisions

### California Regulations

**CCPA/CPRA (2025 Updates)**
- Must specify **exact retention periods** for each data category (not "as long as necessary")
- **5-year minimum** for risk assessment documentation
- **Geolocation data**: Maximum 1 year after last user interaction
- **Penalties**: $7,988 per intentional violation (2025 adjusted amount)

**ADMT (Automated Decision-Making Technology)**
- Compliance required by **January 1, 2027**
- Must disclose use of AI in decisions affecting consumers
- Opt-out rights for profiling

### Recommended Retention Policy for CIRIS

| Data Type | Retention | Justification |
|-----------|-----------|---------------|
| Agent telemetry (metrics) | 30 days detail, 1 year aggregates | Operational needs |
| Agent logs/traces | 14 days | Debugging, not personal data |
| Billing transactions | 7 years | Financial audit requirements |
| Usage logs (with user hash) | 90 days | Service improvement |
| Geolocation (if any) | Do not store | CCPA restriction |
| AI decision logs | 10 years (aggregated) | EU AI Act compliance |

### Key Takeaways

1. **Separate personal data from system logs** - Different retention rules apply
2. **Hash user identifiers** - Already implemented (`user_hash` field)
3. **Document everything** - Retention justification must be written
4. **Billing data is special** - Financial regulations require longer retention than GDPR minimization
5. **EU AI Act is coming** - Plan for 10-year retention of AI system documentation

---

## Questions for Review

1. ~~**Public dashboards**: Do we have users relying on `/lens/` being public? Should we announce the change?~~
   **RESOLVED**: Made private on 2025-12-11

2. **Retention periods**: 30 days for service logs sufficient? Billing audit logs may need 90+ days.
   **UPDATE**: See regulatory research above - billing needs 7 years, usage logs 90 days recommended

3. **Alert destinations**: Where should alerts go? Discord? Email? PagerDuty?

4. **Cross-service trace IDs**: Should we implement OpenTelemetry trace propagation between services?

5. **Log shipping reliability**: Is async batching acceptable, or do we need guaranteed delivery?

---

## References

- [CIRISLens CLAUDE.md](/path/to/CIRISLens/CLAUDE.md)
- [CIRISProxy Logging Plan](/tmp/proxy_logging)
- [Grafana Loki Documentation](https://grafana.com/docs/loki/latest/)
- [TimescaleDB Compression](https://docs.timescale.com/use-timescale/latest/compression/)
