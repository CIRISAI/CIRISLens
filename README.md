# CIRISLens

**Version: 0.2-alpha**

Full-stack observability platform for CIRIS infrastructure - unified metrics, traces, and logs with secure token management and OTLP collection.

## Overview

CIRISLens provides complete observability for CIRIS deployments using best-in-class open source tools:

- **Metrics**: Prometheus + Mimir for 500+ agent metrics
- **Traces**: Tempo for distributed tracing with OTLP support
- **Logs**: Loki for log aggregation with structured metadata
- **Visualization**: Grafana with CIRIS-specific dashboards
- **Collection**: Direct OTLP collection from agent endpoints
- **Security**: Write-only token management with OAuth authentication

## Features

- 🔍 **OTLP Collection**: Direct collection from agent telemetry endpoints
- 🔐 **Secure Token Management**: Write-only token storage in admin UI
- 🏷️ **Taxonomy-Aware**: Automatic categorization of 41 metric sources
- 🔗 **Full Correlation**: Click trace → see logs → view metrics
- 🛡️ **Privacy-First**: Automatic PII sanitization for public dashboards
- 📊 **Rich Dashboards**: Pre-built dashboards for all CIRIS components
- 🚀 **Zero-Code Setup**: Works out of the box with CIRIS agents v1.4.5+

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
- **Grafana**: http://localhost:3000 (admin/admin)
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
| CIRISLens API | Admin interface & token management | 8000 |
| OTLP Collector | Direct agent telemetry collection | Internal |
| OpenTelemetry Collector | Unified ingestion & processing | 4317 (OTLP gRPC), 4318 (OTLP HTTP) |
| Grafana | Visualization & dashboards | 3000 |
| Tempo | Distributed tracing | 3200 |
| Loki | Log aggregation with TSDB | 3100 |
| Mimir | Long-term metrics | 9009 |
| Prometheus | Metrics scraping | 9090 |
| MinIO | Object storage | 9000 (API), 9001 (Console) |
| PostgreSQL | Configuration & telemetry storage | 5432 |

## CIRIS Telemetry Support

CIRISLens collects from CIRIS agents v1.4.5+ with full OTLP support:

### 41 Service Components
- **21 Core Services**: AgentCore, StateManager, CognitiveCore, etc.
- **6 Message Buses**: EventBus, CommandBus, QueryBus, etc.
- **5 Adapters**: HTTPAdapter, WebSocketAdapter, DiscordAdapter, etc.
- **3 Processors**: MessageProcessor, EventProcessor, StateProcessor
- **2 Registries**: ServiceRegistry, AdapterRegistry
- **4 Other Components**: Telemetry, Monitoring, Security, Store

### Collection Intervals
- **Default**: 30 seconds
- **Configurable**: Per-agent via admin UI
- **Retention**: 90 days metrics, 30 days traces, 14 days logs

## Support

- Issues: https://github.com/CIRISAI/CIRISLens/issues
- Community: https://discord.gg/ciris

## License

Apache 2.0 - See [LICENSE](LICENSE) for details
