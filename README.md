# CIRISLens

Full-stack observability platform for CIRIS infrastructure - unified metrics, traces, and logs with taxonomy-aware visualization.

## Overview

CIRISLens provides complete observability for CIRIS deployments using best-in-class open source tools:

- **Metrics**: Prometheus + Mimir for 500+ agent metrics
- **Traces**: Tempo for distributed tracing
- **Logs**: Loki for log aggregation
- **Visualization**: Grafana with CIRIS-specific dashboards
- **Collection**: OpenTelemetry Collector with CIRIS taxonomy processors

## Features

- 🔍 **Unified Telemetry**: Single endpoint collection from CIRIS agents (v1.4.3+)
- 🏷️ **Taxonomy-Aware**: Automatic categorization of 45 metric sources
- 🔗 **Full Correlation**: Click trace → see logs → view metrics
- 🛡️ **Privacy-First**: Automatic PII sanitization for public dashboards
- 📊 **Rich Dashboards**: Pre-built dashboards for all CIRIS components
- 🚀 **Zero-Code Setup**: Works out of the box with CIRIS agents

## Quick Start

### Prerequisites

- Docker & Docker Compose
- CIRIS agents running v1.4.3 or later
- 4GB RAM minimum (8GB recommended)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/CIRISAI/CIRISLens.git
cd CIRISLens
```

2. Start the stack:
```bash
docker-compose up -d
```

3. Wait for services to initialize (2-3 minutes):
```bash
docker-compose ps
```

4. Access the dashboards:
- **Grafana**: http://localhost:3000 (admin/admin)
- **MinIO Console**: http://localhost:9001 (admin/adminpassword123)

### Configure Agent Discovery

CIRISLens automatically discovers agents via Docker labels. Ensure your agents have:

```yaml
labels:
  ciris.agent: "true"
  ciris.agent_id: "${AGENT_ID}"
  ciris.template: "${TEMPLATE}"
  ciris.version: "${VERSION}"
```

## Architecture

```
CIRIS Agents → OpenTelemetry Collector → Storage Backends → Grafana
                     ↓                         ↓
              (Taxonomy Processing)    (Tempo/Loki/Mimir)
```

### Components

| Component | Purpose | Port |
|-----------|---------|------|
| OpenTelemetry Collector | Unified ingestion & processing | 4317 (OTLP gRPC), 4318 (OTLP HTTP) |
| Grafana | Visualization & dashboards | 3000 |
| Tempo | Distributed tracing | 3200 |
| Loki | Log aggregation | 3100 |
| Mimir | Long-term metrics | 9009 |
| Prometheus | Legacy metrics scraping | 9090 |
| MinIO | Object storage | 9000 (API), 9001 (Console) |

## CIRIS Taxonomy

CIRISLens understands the CIRIS component taxonomy:

- **21 Core Services**: Agent core, state management, cognitive systems
- **6 Message Buses**: Event routing and command handling
- **5 Adapters**: External integrations
- **3 Processors**: Data transformation
- **2 Registries**: Service and configuration management
- **8 Other Components**: Supporting services

All telemetry is automatically enriched with taxonomy metadata for intelligent filtering and visualization.

## Support

- Issues: https://github.com/CIRISAI/CIRISLens/issues
- Community: https://discord.gg/ciris

## License

Apache 2.0 - See [LICENSE](LICENSE) for details
