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
- **DO**: Sanitize PII in OpenTelemetry Collector
- **DO**: Hash agent IDs for public dashboards
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
# SSH to production server
ssh -i ~/.ssh/ciris_deploy root@observability.ciris.ai

# CIRISLens location
cd /opt/cirislens

# View running services
docker-compose ps

# Check logs
docker-compose logs -f grafana
docker-compose logs -f otel-collector

# Restart services
docker-compose restart
```

### Production URLs
- **Grafana**: https://lens.ciris.ai (behind Cloudflare)
- **Public Dashboard**: https://lens.ciris.ai/public/
- **Prometheus**: Internal only (port 9090)
- **OTLP Endpoint**: grpc://observability.ciris.ai:4317

### Production Configuration

The production deployment uses:
- **Cloudflare**: DNS and SSL termination
- **S3/MinIO**: Long-term storage for metrics/traces/logs
- **High Retention**: 90 days metrics, 30 days traces, 14 days logs
- **Auth**: Google OAuth for private dashboards

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

Check storage usage:
```bash
docker system df
docker volume ls
docker volume prune  # Clean unused volumes
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

- **Never expose raw Prometheus/Loki/Tempo ports publicly**
- **Always use Grafana as the gateway**
- **Enable auth for production deployments**
- **Sanitize data in collector, not dashboards**
- **Use read-only datasources where possible**

## Future Enhancements

Potential additions (not yet implemented):
- Grafana OnCall for incident management
- Grafana k6 for load testing
- Grafana Faro for frontend monitoring
- Custom Grafana plugin for CIRIS topology visualization
- ML-based anomaly detection on metrics