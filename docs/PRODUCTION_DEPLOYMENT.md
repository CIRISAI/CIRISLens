# CIRISLens Production Deployment on agents.ciris.ai

## Integration Strategy

CIRISLens would integrate into the existing agents.ciris.ai infrastructure as a parallel observability stack that collects telemetry without interfering with production operations.

## Proposed Architecture

### Port Assignments

| Service                | Container Name           | Host Port | Purpose                        |
|------------------------|-------------------------|-----------|--------------------------------|
| CIRISLens API          | cirislens-api          | 8200      | Admin API & token management   |
| Grafana                | cirislens-grafana      | 3001      | Visualization (internal)       |
| Prometheus             | cirislens-prometheus   | 9090      | Metrics scraping (internal)    |
| Loki                   | cirislens-loki         | 3100      | Log aggregation (internal)     |
| Tempo                  | cirislens-tempo        | 3200      | Trace storage (internal)       |
| Mimir                  | cirislens-mimir        | 9009      | Long-term metrics (internal)   |
| OTLP Collector         | cirislens-collector    | 4317-4318 | OTLP ingestion (internal)      |
| MinIO                  | cirislens-minio        | 9000-9001 | Object storage (internal)      |
| PostgreSQL             | cirislens-db           | 5433      | Config database (internal)     |

### Network Configuration

```yaml
# Add CIRISLens to existing ciris-network
networks:
  ciris-network:
    external: true
  cirislens-internal:
    driver: bridge
    internal: true
```

### Nginx Routing Updates

Add to existing nginx configuration:

```nginx
# CIRISLens Public Dashboard (read-only)
location /lens/ {
    proxy_pass http://cirislens-grafana:3000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    
    # Public access, no auth required for dashboards
    allow all;
}

# CIRISLens Admin (authenticated)
location /lens/admin/ {
    proxy_pass http://cirislens-api:8200/admin/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    
    # OAuth will handle authentication
    allow all;
}

# Block direct access to internal services
location ~ ^/(prometheus|loki|tempo|mimir|minio)/ {
    return 403;
}
```

## Deployment Steps

### 1. Prepare Environment

```bash
# SSH to production server
ssh root@agents.ciris.ai

# Create CIRISLens directory
mkdir -p /opt/cirislens
cd /opt/cirislens

# Clone repository
git clone https://github.com/CIRISAI/CIRISLens.git .
```

### 2. Configure Production Environment

```bash
# Create production .env
cat > .env << 'EOF'
# Production Configuration
ENV=production
DATABASE_URL=postgresql://cirislens:SECURE_PASSWORD@cirislens-db:5432/cirislens

# OAuth (using existing Cloudflare/Google setup)
GOOGLE_CLIENT_ID=your-production-client-id
GOOGLE_CLIENT_SECRET=your-production-secret
OAUTH_CALLBACK_URL=https://agents.ciris.ai/lens/admin/auth/callback
ALLOWED_DOMAIN=ciris.ai
SESSION_SECRET=$(openssl rand -hex 32)

# Collection Settings
COLLECTION_INTERVAL_SECONDS=30
OTLP_COLLECTION_ENABLED=true

# Agent tokens will be added via Admin UI
EOF
```

### 3. Create Production Docker Compose Override

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  cirislens-api:
    networks:
      - ciris-network  # Connect to main network for agent access
      - cirislens-internal
    environment:
      - MANAGER_API_URL=http://host.docker.internal:8888/manager/v1
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Access host manager

  cirislens-grafana:
    networks:
      - ciris-network  # For nginx access
      - cirislens-internal
    environment:
      - GF_SERVER_ROOT_URL=https://agents.ciris.ai/lens/
      - GF_SERVER_SERVE_FROM_SUB_PATH=true
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
      
  # All other services on internal network only
  cirislens-collector:
    networks:
      - cirislens-internal
      
  cirislens-db:
    networks:
      - cirislens-internal
    ports:
      - "127.0.0.1:5433:5432"  # Local access only

networks:
  ciris-network:
    external: true
  cirislens-internal:
    driver: bridge
    internal: true
```

### 4. Launch CIRISLens

```bash
# Start the stack
docker compose -f docker-compose.managed.yml -f docker-compose.prod.yml up -d

# Verify services
docker compose ps

# Check logs
docker compose logs -f
```

### 5. Configure Agent Collection

Since agents are on the same host, CIRISLens can collect directly:

```yaml
# Agent URLs for internal collection
AGENT_DATUM_URL=http://ciris-datum:8080
AGENT_SAGE_URL=http://ciris-sage-2wnuc8:8080
```

## Data Collection Flow

```
Production Agents → OTLP Endpoints → CIRISLens Collector → Storage → Grafana
     ↓                    ↓                   ↓
(Port 8001/8003)    (Internal Access)   (PostgreSQL/MinIO)
```

### Collection Methods

1. **Direct OTLP Collection** (Preferred)
   - CIRISLens API connects directly to agent OTLP endpoints
   - Uses service tokens for authentication
   - No impact on production traffic

2. **Manager Integration** 
   - Query Manager API for agent discovery
   - Automatic agent detection and enrollment
   - Centralized configuration

## Security Considerations

### Network Isolation
- CIRISLens internal services on isolated network
- Only API and Grafana exposed to ciris-network
- No external ports except through nginx

### Authentication
- Admin UI requires Google OAuth (@ciris.ai domain)
- Public dashboards are read-only with PII redacted
- Service tokens stored encrypted, write-only access

### Resource Limits

```yaml
# Add to production override
services:
  cirislens-mimir:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1.0'
        reservations:
          memory: 1G
          
  cirislens-grafana:
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.5'
```

## Monitoring CIRISLens Itself

```bash
# Health checks
curl http://localhost:8200/health  # API health
curl http://localhost:3001/api/health  # Grafana health

# Resource usage
docker stats cirislens-*

# Disk usage
du -sh /var/lib/docker/volumes/cirislens_*
```

## Backup Strategy

```bash
# Daily backup script
#!/bin/bash
BACKUP_DIR=/backups/cirislens/$(date +%Y%m%d)
mkdir -p $BACKUP_DIR

# Backup PostgreSQL
docker compose exec cirislens-db pg_dump -U cirislens cirislens | gzip > $BACKUP_DIR/cirislens.sql.gz

# Backup Grafana dashboards
docker compose exec cirislens-grafana tar czf - /var/lib/grafana/dashboards > $BACKUP_DIR/dashboards.tar.gz

# Backup configurations
tar czf $BACKUP_DIR/configs.tar.gz config/ sql/ .env

# Rotate old backups (keep 30 days)
find /backups/cirislens -type d -mtime +30 -exec rm -rf {} +
```

## Public Access URLs

After deployment, CIRISLens would be accessible at:

- **Public Dashboards**: https://agents.ciris.ai/lens/
- **Admin Interface**: https://agents.ciris.ai/lens/admin/
- **API Health**: https://agents.ciris.ai/lens/api/health

## Rollback Plan

If issues arise:

```bash
# Stop CIRISLens without affecting agents
cd /opt/cirislens
docker compose down

# Remove from nginx if needed
# Comment out /lens/* locations in nginx config
nginx -s reload
```

## Resource Requirements

- **CPU**: 2-4 cores recommended
- **RAM**: 4-8 GB (depending on retention)
- **Disk**: 50-100 GB for telemetry storage
- **Network**: Minimal bandwidth (telemetry is compressed)

## Advantages of This Approach

1. **Zero Impact**: No changes to existing agents or manager
2. **Isolated**: Separate network and resources
3. **Scalable**: Can add more agents without reconfiguration
4. **Secure**: Leverages existing auth infrastructure
5. **Reversible**: Easy to remove without affecting production