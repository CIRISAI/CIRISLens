# Status API Requirements

## Overview

Each CIRIS service exposes a `/status` endpoint reporting the health of its dependencies. CIRISLens aggregates these into a unified status API for the public status page at ciris.ai/status.

**Principle**: Secrets stay where they're used. Each service checks its own providers.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  CIRISBilling   │     │   CIRISProxy    │     │   CIRISLens     │
│  /v1/status     │     │   /v1/status    │     │   /v1/status    │
│                 │     │                 │     │                 │
│ - Google OAuth  │     │ - OpenRouter    │     │ - PostgreSQL    │
│ - Google Play   │     │ - Groq          │     │ - Grafana       │
│ - PostgreSQL    │     │ - Together AI   │     │                 │
│                 │     │ - OpenAI        │     │                 │
│                 │     │ - Brave Search  │     │                 │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      CIRISLens          │
                    │   /api/v1/status        │
                    │   (aggregator)          │
                    │                         │
                    │ + Vultr (ping)          │
                    │ + Hetzner (ping)        │
                    │ + Cloudflare (DNS)      │
                    │ + GitHub GHCR (ping)    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   ciris.ai/status       │
                    │   (frontend)            │
                    └─────────────────────────┘
```

## Service Status Endpoints

### 1. CIRISBilling `/v1/status`

**Checks:**
| Provider | Check Method | Timeout |
|----------|--------------|---------|
| PostgreSQL | Connection query | 5s |
| Google OAuth | Token endpoint ping | 5s |
| Google Play | Developer API ping | 5s |

**Response:**
```json
{
  "service": "cirisbilling",
  "status": "operational",
  "timestamp": "2025-12-14T00:00:00Z",
  "version": "1.0.0",
  "providers": {
    "postgresql": {
      "status": "operational",
      "latency_ms": 12,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "google_oauth": {
      "status": "operational",
      "latency_ms": 45,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "google_play": {
      "status": "operational",
      "latency_ms": 78,
      "last_check": "2025-12-14T00:00:00Z"
    }
  }
}
```

### 2. CIRISProxy `/v1/status`

**Checks:**
| Provider | Check Method | Timeout |
|----------|--------------|---------|
| OpenRouter | `/api/v1/models` endpoint | 5s |
| Groq | `/openai/v1/models` endpoint | 5s |
| Together AI | `/v1/models` endpoint | 5s |
| OpenAI | `/v1/models` endpoint | 5s |
| Brave Search | API health check | 5s |

**Response:**
```json
{
  "service": "cirisproxy",
  "status": "degraded",
  "timestamp": "2025-12-14T00:00:00Z",
  "version": "1.0.0",
  "providers": {
    "openrouter": {
      "status": "operational",
      "latency_ms": 120,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "groq": {
      "status": "operational",
      "latency_ms": 89,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "together_ai": {
      "status": "degraded",
      "latency_ms": 2500,
      "last_check": "2025-12-14T00:00:00Z",
      "message": "High latency"
    },
    "openai": {
      "status": "operational",
      "latency_ms": 156,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "brave_search": {
      "status": "operational",
      "latency_ms": 234,
      "last_check": "2025-12-14T00:00:00Z"
    }
  }
}
```

### 3. CIRISLens `/v1/status`

**Checks:**
| Provider | Check Method | Timeout |
|----------|--------------|---------|
| PostgreSQL | Connection query | 5s |
| Grafana | `/api/health` endpoint | 5s |

**Response:**
```json
{
  "service": "cirislens",
  "status": "operational",
  "timestamp": "2025-12-14T00:00:00Z",
  "version": "1.0.0",
  "providers": {
    "postgresql": {
      "status": "operational",
      "latency_ms": 8,
      "last_check": "2025-12-14T00:00:00Z"
    },
    "grafana": {
      "status": "operational",
      "latency_ms": 23,
      "last_check": "2025-12-14T00:00:00Z"
    }
  }
}
```

## CIRISLens Aggregator `/api/v1/status`

CIRISLens aggregates all service statuses plus infrastructure checks.

**Additional Infrastructure Checks (performed by CIRISLens):**
| Provider | Check Method | Timeout |
|----------|--------------|---------|
| Vultr US | HTTPS ping to service | 5s |
| Hetzner EU | HTTPS ping to service | 5s |
| Cloudflare | DNS resolution check | 5s |
| GitHub GHCR | Registry API ping | 5s |

**Aggregated Response:**
```json
{
  "status": "operational",
  "timestamp": "2025-12-14T00:00:00Z",
  "last_incident": null,
  "services": {
    "billing": {
      "name": "Billing & Authentication",
      "status": "operational",
      "url": "https://billing.ciris-services-1.ai/v1/status"
    },
    "proxy": {
      "name": "LLM Proxy",
      "status": "operational",
      "url": "https://proxy.ciris-services-1.ai/v1/status"
    },
    "lens": {
      "name": "Observability",
      "status": "operational",
      "url": "https://lens.ciris-services-1.ai/v1/status"
    }
  },
  "infrastructure": {
    "us_region": {
      "name": "US Region (Chicago)",
      "status": "operational",
      "provider": "vultr"
    },
    "eu_region": {
      "name": "EU Region (Germany)",
      "status": "operational",
      "provider": "hetzner"
    },
    "dns": {
      "name": "DNS & CDN",
      "status": "operational",
      "provider": "cloudflare"
    },
    "container_registry": {
      "name": "Container Registry",
      "status": "operational",
      "provider": "github"
    }
  },
  "llm_providers": {
    "openrouter": {"status": "operational", "latency_ms": 120},
    "groq": {"status": "operational", "latency_ms": 89},
    "together_ai": {"status": "degraded", "latency_ms": 2500},
    "openai": {"status": "operational", "latency_ms": 156}
  }
}
```

## Data Model for Historical Tracking

### Status Check Table

```sql
CREATE TABLE cirislens.status_checks (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    service_name VARCHAR(50) NOT NULL,      -- 'cirisbilling', 'cirisproxy', 'cirislens'
    provider_name VARCHAR(50) NOT NULL,     -- 'postgresql', 'openrouter', etc.
    status VARCHAR(20) NOT NULL,            -- 'operational', 'degraded', 'outage'
    latency_ms INTEGER,
    error_message TEXT,
    PRIMARY KEY (id, timestamp)
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('cirislens.status_checks', 'timestamp');

-- Retention: 90 days of detailed data
SELECT add_retention_policy('cirislens.status_checks', INTERVAL '90 days');

-- Compression after 7 days
SELECT add_compression_policy('cirislens.status_checks', INTERVAL '7 days');
```

### Continuous Aggregates for Uptime Calculation

```sql
-- Hourly availability percentages
CREATE MATERIALIZED VIEW cirislens.status_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp) AS hour,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / COUNT(*) AS uptime_pct,
    AVG(latency_ms) AS avg_latency_ms,
    MAX(latency_ms) AS max_latency_ms,
    COUNT(*) AS check_count
FROM cirislens.status_checks
GROUP BY hour, service_name, provider_name;

-- Refresh hourly
SELECT add_continuous_aggregate_policy('cirislens.status_hourly',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Daily availability (for 30/90 day uptime display)
CREATE MATERIALIZED VIEW cirislens.status_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', timestamp) AS day,
    service_name,
    provider_name,
    COUNT(*) FILTER (WHERE status = 'operational') * 100.0 / COUNT(*) AS uptime_pct,
    AVG(latency_ms) AS avg_latency_ms,
    COUNT(*) FILTER (WHERE status = 'outage') AS outage_count
FROM cirislens.status_checks
GROUP BY day, service_name, provider_name;

-- Retain daily aggregates for 1 year
SELECT add_retention_policy('cirislens.status_daily', INTERVAL '365 days');
```

## Check Cadence

| Check Type | Interval | Rationale |
|------------|----------|-----------|
| Service status endpoints | 60 seconds | Balance between freshness and load |
| Infrastructure pings | 60 seconds | Same cadence for consistency |
| LLM provider checks | 60 seconds | Via CIRISProxy aggregation |

**Implementation**: CIRISLens runs a background task that:
1. Every 60s: Fetches `/v1/status` from each service
2. Every 60s: Performs infrastructure checks
3. Stores results in `status_checks` table
4. Caches latest status in memory (for fast API response)

## Status Levels

| Status | Definition | Display Color |
|--------|------------|---------------|
| `operational` | All checks passing, latency < 1000ms | Green |
| `degraded` | Checks passing but latency > 1000ms, or partial failures | Yellow |
| `outage` | Check failed or timeout | Red |
| `maintenance` | Planned downtime (manual flag) | Blue |

**Overall Status Calculation:**
- `operational`: All services operational
- `degraded`: Any service degraded, none in outage
- `partial_outage`: 1-2 services in outage
- `major_outage`: 3+ services in outage

## API Endpoints Summary

### Public (No Auth)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/status` | Current aggregated status |
| `GET /api/v1/status/history?days=30` | Historical uptime data |

### Per-Service (No Auth)

| Service | Endpoint |
|---------|----------|
| CIRISBilling | `GET /v1/status` |
| CIRISProxy | `GET /v1/status` |
| CIRISLens | `GET /v1/status` |

## Implementation Order

1. **CIRISLens**: Add `/v1/status` endpoint (local checks only)
2. **CIRISLens**: Add status_checks table and collection task
3. **CIRISBilling**: Add `/v1/status` endpoint
4. **CIRISProxy**: Add `/v1/status` endpoint
5. **CIRISLens**: Add aggregator `/api/v1/status` endpoint
6. **CIRISLens**: Add `/api/v1/status/history` endpoint
7. **ciris-website**: Build status page UI

## Security Considerations

- Status endpoints are **public** (no auth required)
- **Never expose**: API keys, internal IPs, error details, stack traces
- **Safe to expose**: Status, latency, timestamps, provider names
- Rate limit status endpoints: 60 req/min per IP
