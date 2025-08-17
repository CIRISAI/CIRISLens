# Troubleshooting Guide

## Common Issues and Solutions

### 1. No Data Appearing in Grafana

#### Symptom
Dashboards show "No Data" or empty graphs.

#### Diagnosis
```bash
# Check if agents are sending data
docker-compose logs otel-collector | grep "datapoints"

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets'

# Verify agent metrics endpoint
curl http://[AGENT_HOST]:8080/v1/telemetry/unified?format=prometheus
```

#### Solutions

**Agent not configured for telemetry:**
```yaml
# Add to agent environment
TELEMETRY_ENABLED: "true"
TELEMETRY_ENDPOINT: "http://observability.ciris.ai:4318"
```

**Firewall blocking connections:**
```bash
# On CIRISLens server
sudo ufw allow 4317/tcp  # OTLP gRPC
sudo ufw allow 4318/tcp  # OTLP HTTP
```

**Wrong datasource in Grafana:**
- Go to Configuration → Data Sources
- Verify Mimir URL is `http://mimir:9009/prometheus`
- Click "Save & Test"

### 2. High Memory Usage

#### Symptom
Docker containers using excessive memory, system becoming slow.

#### Diagnosis
```bash
# Check memory usage
docker stats --no-stream

# Find memory leaks
docker-compose logs mimir | grep "heap"
```

#### Solutions

**Adjust retention policies:**
```yaml
# In config/mimir.yaml
limits:
  compactor_blocks_retention_period: 7d  # Reduce from 30d

# In config/loki.yaml
limits_config:
  retention_period: 72h  # Reduce from 168h
```

**Increase swap space:**
```bash
# Create 4GB swap file
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

**Restart memory-heavy services:**
```bash
docker-compose restart mimir
docker-compose restart tempo
```

### 3. Disk Space Issues

#### Symptom
"No space left on device" errors.

#### Diagnosis
```bash
# Check disk usage
df -h
docker system df

# Find large directories
du -sh /var/lib/docker/*
ncdu /var/lib/docker
```

#### Solutions

**Clean up Docker:**
```bash
# Remove unused images and volumes
docker system prune -a --volumes

# Remove old logs
truncate -s 0 /var/lib/docker/containers/*/*-json.log
```

**Move data to external storage:**
```yaml
# In docker-compose.yml
volumes:
  mimir_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /mnt/external/mimir  # External mount
```

### 4. Traces Not Correlating with Logs

#### Symptom
"View Logs" button in Tempo doesn't show related logs.

#### Diagnosis
```bash
# Check if trace_id is in logs
docker-compose logs loki | grep "trace_id"

# Verify correlation config in Grafana
curl http://localhost:3000/api/datasources/name/Tempo | jq '.jsonData.tracesToLogsV2'
```

#### Solutions

**Fix trace context propagation:**
```yaml
# In agent configuration
OTEL_PROPAGATORS: "tracecontext,baggage"
OTEL_TRACES_EXPORTER: "otlp"
```

**Update Loki labels:**
```yaml
# In config/otel-collector.yaml
exporters:
  loki:
    labels:
      attributes:
        trace_id: "trace_id"
        span_id: "span_id"
```

### 5. Slow Query Performance

#### Symptom
Dashboards take long to load, queries timeout.

#### Diagnosis
```bash
# Check query performance
curl -s http://localhost:9009/api/v1/query_range \
  -d 'query=up' \
  -d 'start=2024-01-01T00:00:00Z' \
  -d 'end=2024-01-01T01:00:00Z' | jq '.data.stats'
```

#### Solutions

**Add recording rules:**
```yaml
# In config/mimir-rules.yaml
groups:
  - name: ciris_aggregates
    interval: 1m
    rules:
      - record: ciris:agent_health_rate
        expr: avg by (agent_id) (up{job="ciris-agents"})
```

**Optimize queries:**
```promql
# Bad - scans all data
sum(ciris_agent_messages_total)

# Good - uses rate and time range
sum(rate(ciris_agent_messages_total[5m]))
```

**Increase query cache:**
```yaml
# In config/mimir.yaml
frontend:
  cache_results: true
  results_cache:
    backend: memcached
    memcached:
      expiration: 1h
```

### 6. Container Restart Loops

#### Symptom
Containers continuously restarting.

#### Diagnosis
```bash
# Check container status
docker-compose ps

# View logs of failing container
docker-compose logs --tail=100 [SERVICE_NAME]

# Check exit codes
docker inspect [CONTAINER_NAME] | jq '.[0].State'
```

#### Solutions

**Configuration errors:**
```bash
# Validate YAML files
docker-compose config

# Check specific config
docker run --rm -v $(pwd)/config:/config grafana/mimir:latest \
  -config.verify -config.file=/config/mimir.yaml
```

**Port conflicts:**
```bash
# Find port usage
sudo netstat -tulpn | grep LISTEN

# Change port in docker-compose.yml
ports:
  - "3001:3000"  # Changed from 3000:3000
```

### 7. Authentication Issues

#### Symptom
Can't log into Grafana, OAuth not working.

#### Diagnosis
```bash
# Check Grafana auth config
docker-compose exec grafana cat /etc/grafana/grafana.ini | grep -A5 "\[auth"

# View auth logs
docker-compose logs grafana | grep "auth"
```

#### Solutions

**Reset admin password:**
```bash
docker-compose exec grafana grafana-cli admin reset-admin-password newpassword
```

**Fix OAuth callback URL:**
```yaml
# In .env
GF_SERVER_ROOT_URL=https://lens.ciris.ai  # Must match OAuth redirect URI
```

### 8. Missing Metrics

#### Symptom
Some metrics not appearing despite agents running.

#### Diagnosis
```bash
# List all metrics
curl -s http://localhost:9090/api/v1/label/__name__/values | jq '.data[]' | grep ciris

# Check specific metric
curl -s http://localhost:9090/api/v1/query?query=ciris_agent_cognitive_state
```

#### Solutions

**Agent version too old:**
```bash
# Check agent version
curl http://[AGENT_HOST]:8080/v1/system/version

# Upgrade to 1.4.3+
docker pull ghcr.io/cirisai/ciris-agent:latest
```

**Metric name changed:**
```promql
# Old name
ciris_cognitive_state

# New name (v1.4.3+)
ciris_agent_cognitive_state
```

### 9. Network Connectivity Issues

#### Symptom
Agents can't connect to CIRISLens.

#### Diagnosis
```bash
# Test connectivity from agent
curl -v http://observability.ciris.ai:4318/v1/metrics

# Check firewall rules
sudo iptables -L -n -v

# Verify DNS resolution
nslookup observability.ciris.ai
```

#### Solutions

**Use IP instead of hostname:**
```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: "http://123.45.67.89:4318"
```

**Add to hosts file:**
```bash
echo "123.45.67.89 observability.ciris.ai" >> /etc/hosts
```

### 10. Data Not Persisting

#### Symptom
Data disappears after restart.

#### Diagnosis
```bash
# Check volume mounts
docker volume ls
docker volume inspect cirislens_mimir_data

# Verify data directories
ls -la /var/lib/docker/volumes/cirislens_*/_data
```

#### Solutions

**Fix volume permissions:**
```bash
# Set correct ownership
sudo chown -R 10001:10001 /var/lib/docker/volumes/cirislens_mimir_data
sudo chown -R 10001:10001 /var/lib/docker/volumes/cirislens_loki_data
```

**Backup before restart:**
```bash
# Create backup
docker run --rm -v cirislens_grafana_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/grafana_backup.tar.gz -C /data .
```

## Performance Optimization

### Query Optimization

**Use recording rules for expensive queries:**
```yaml
# config/mimir-rules.yaml
groups:
  - name: ciris_performance
    rules:
      - record: ciris:message_rate_5m
        expr: sum(rate(ciris_messagebus_messages_total[5m])) by (agent_id)
```

### Resource Tuning

**Adjust batch sizes:**
```yaml
# config/otel-collector.yaml
processors:
  batch:
    timeout: 5s  # Increase for better batching
    send_batch_size: 2048  # Increase for efficiency
```

### Storage Optimization

**Enable compression:**
```yaml
# config/mimir.yaml
blocks_storage:
  tsdb:
    block_ranges_period: [2h]
    retention_period: 30d
    compression: snappy  # or zstd for better compression
```

## Getting Help

### Logs Location

- **Container logs**: `docker-compose logs [SERVICE]`
- **System logs**: `/var/log/syslog`
- **Nginx logs**: `/var/log/nginx/`

### Debug Mode

Enable debug logging:
```yaml
# In .env
LOG_LEVEL=debug
OTEL_LOG_LEVEL=debug
GRAFANA_LOG_LEVEL=debug
```

### Support Channels

1. **GitHub Issues**: https://github.com/CIRISAI/CIRISLens/issues
2. **Discord**: https://discord.gg/ciris
3. **Logs**: Attach output of `docker-compose logs --tail=1000`

### Health Check Script

Create `healthcheck.sh`:
```bash
#!/bin/bash
echo "=== CIRISLens Health Check ==="
echo "Grafana: $(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health)"
echo "Tempo: $(curl -s -o /dev/null -w "%{http_code}" http://localhost:3200/ready)"
echo "Loki: $(curl -s -o /dev/null -w "%{http_code}" http://localhost:3100/ready)"
echo "Mimir: $(curl -s -o /dev/null -w "%{http_code}" http://localhost:9009/ready)"
echo "OTel: $(curl -s -o /dev/null -w "%{http_code}" http://localhost:8888/metrics)"
```