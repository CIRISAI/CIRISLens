# Production Deployment Guide

## Server Requirements

### Minimum Specifications
- **CPU**: 4 cores
- **RAM**: 8GB
- **Storage**: 100GB SSD
- **Network**: 1Gbps
- **OS**: Ubuntu 22.04 LTS

### Recommended Specifications
- **CPU**: 8 cores
- **RAM**: 16GB
- **Storage**: 500GB SSD (or S3/MinIO)
- **Network**: 10Gbps
- **OS**: Ubuntu 22.04 LTS

## Initial Server Setup

### 1. Create Server (Vultr Example)

```bash
# Via Vultr CLI or web console
vultr instance create \
  --region ewr \
  --plan vhp-8c-16gb-amd \
  --os 1743 \
  --label "cirislens-prod" \
  --hostname "observability.ciris.ai"
```

### 2. DNS Configuration

Add these records to your DNS (Cloudflare):

```
A     observability.ciris.ai  → [SERVER_IP]  (Proxied: No)
CNAME lens.ciris.ai          → observability.ciris.ai (Proxied: Yes)
```

### 3. SSH Access

```bash
# Add SSH key
ssh-copy-id -i ~/.ssh/ciris_deploy.pub root@[SERVER_IP]

# Test connection
ssh -i ~/.ssh/ciris_deploy root@observability.ciris.ai
```

## Installation

### 1. System Prerequisites

```bash
# Update system
apt update && apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install essential tools
apt install -y git htop ncdu ufw fail2ban
```

### 2. Firewall Configuration

```bash
# Configure UFW
ufw default deny incoming
ufw default allow outgoing

# SSH
ufw allow 22/tcp

# Grafana (via reverse proxy)
ufw allow 80/tcp
ufw allow 443/tcp

# OpenTelemetry endpoints
ufw allow 4317/tcp  # OTLP gRPC
ufw allow 4318/tcp  # OTLP HTTP

# Internal services (only if needed)
# ufw allow from [CIRIS_MANAGER_IP] to any port 9090  # Prometheus
# ufw allow from [CIRIS_MANAGER_IP] to any port 3100  # Loki

ufw enable
```

### 3. Deploy CIRISLens

```bash
# Clone repository
cd /opt
git clone https://github.com/CIRISAI/CIRISLens.git cirislens
cd cirislens

# Create production environment file
cp .env.example .env.production
```

### 4. Production Configuration

Edit `.env.production`:

```bash
# Grafana Configuration
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=[STRONG_PASSWORD]
GF_SERVER_DOMAIN=lens.ciris.ai
GF_SERVER_ROOT_URL=https://lens.ciris.ai
GF_ANALYTICS_REPORTING_ENABLED=false

# Google OAuth (for private dashboards)
GF_AUTH_GOOGLE_ENABLED=true
GF_AUTH_GOOGLE_CLIENT_ID=[YOUR_CLIENT_ID]
GF_AUTH_GOOGLE_CLIENT_SECRET=[YOUR_CLIENT_SECRET]
GF_AUTH_GOOGLE_ALLOWED_DOMAINS=ciris.ai

# MinIO (Object Storage)
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=[STRONG_PASSWORD]
MINIO_BROWSER=off  # Disable console in production

# Storage Retention
MIMIR_RETENTION_DAYS=90
TEMPO_RETENTION_DAYS=30
LOKI_RETENTION_DAYS=14
```

### 5. Production Docker Compose

Create `docker-compose.production.yml`:

```yaml
version: '3.8'

services:
  # Override configurations for production
  
  grafana:
    restart: always
    volumes:
      - grafana_data:/var/lib/grafana
      - ./config/grafana/grafana.ini:/etc/grafana/grafana.ini:ro
    environment:
      - GF_INSTALL_PLUGINS=grafana-piechart-panel,grafana-worldmap-panel
      - GF_SERVER_ENABLE_GZIP=true
    deploy:
      resources:
        limits:
          memory: 2G
          
  mimir:
    restart: always
    volumes:
      - mimir_data:/data
    deploy:
      resources:
        limits:
          memory: 4G
          
  tempo:
    restart: always
    volumes:
      - tempo_data:/data
    deploy:
      resources:
        limits:
          memory: 2G
          
  loki:
    restart: always
    volumes:
      - loki_data:/loki
    deploy:
      resources:
        limits:
          memory: 2G

volumes:
  grafana_data:
    driver: local
  mimir_data:
    driver: local
  tempo_data:
    driver: local
  loki_data:
    driver: local
```

### 6. Start Services

```bash
# Use both compose files
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f
```

## Nginx Reverse Proxy

### 1. Install Nginx

```bash
apt install -y nginx certbot python3-certbot-nginx
```

### 2. Configure Nginx

Create `/etc/nginx/sites-available/cirislens`:

```nginx
server {
    listen 80;
    server_name lens.ciris.ai observability.ciris.ai;
    
    location / {
        return 301 https://$server_name$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name lens.ciris.ai observability.ciris.ai;
    
    # SSL (will be managed by Certbot)
    ssl_certificate /etc/letsencrypt/live/lens.ciris.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lens.ciris.ai/privkey.pem;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    # Grafana
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support for live updates
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # OTLP HTTP endpoint (for agents)
    location /v1/traces {
        proxy_pass http://localhost:4318;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /v1/metrics {
        proxy_pass http://localhost:4318;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /v1/logs {
        proxy_pass http://localhost:4318;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. Enable Site and Get SSL

```bash
# Enable site
ln -s /etc/nginx/sites-available/cirislens /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

# Get SSL certificate
certbot --nginx -d lens.ciris.ai -d observability.ciris.ai
```

## Connecting Agents

### Agent Configuration

In CIRISAgent's environment:

```yaml
# For OTLP/gRPC (recommended)
OTEL_EXPORTER_OTLP_ENDPOINT: "grpc://observability.ciris.ai:4317"
OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"

# For OTLP/HTTP (firewall-friendly)
OTEL_EXPORTER_OTLP_ENDPOINT: "https://lens.ciris.ai"
OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"

# Common settings
OTEL_SERVICE_NAME: "${AGENT_NAME}"
OTEL_RESOURCE_ATTRIBUTES: "agent.id=${AGENT_ID},agent.template=${TEMPLATE},deployment.environment=production"
```

### Manager Configuration

In CIRISManager's configuration:

```yaml
telemetry:
  endpoint: "https://lens.ciris.ai"
  export_interval: 60
  resource_attributes:
    service.name: "ciris-manager"
    deployment.environment: "production"
```

## Backup and Recovery

### Automated Backups

Create `/opt/cirislens/backup.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/backups/cirislens"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p $BACKUP_DIR

# Backup Grafana dashboards and config
docker run --rm -v grafana_data:/data -v $BACKUP_DIR:/backup \
  alpine tar czf /backup/grafana_$DATE.tar.gz -C /data .

# Backup metrics data
docker run --rm -v mimir_data:/data -v $BACKUP_DIR:/backup \
  alpine tar czf /backup/mimir_$DATE.tar.gz -C /data .

# Keep only last 7 days
find $BACKUP_DIR -name "*.tar.gz" -mtime +7 -delete
```

Add to crontab:

```bash
0 2 * * * /opt/cirislens/backup.sh
```

### Restore from Backup

```bash
# Stop services
docker-compose down

# Restore Grafana
docker run --rm -v grafana_data:/data -v /backups/cirislens:/backup \
  alpine tar xzf /backup/grafana_[DATE].tar.gz -C /data

# Restore metrics
docker run --rm -v mimir_data:/data -v /backups/cirislens:/backup \
  alpine tar xzf /backup/mimir_[DATE].tar.gz -C /data

# Restart services
docker-compose up -d
```

## Monitoring CIRISLens

### Health Checks

```bash
# Check all services are running
curl -s http://localhost:3000/api/health          # Grafana
curl -s http://localhost:3200/ready              # Tempo
curl -s http://localhost:3100/ready              # Loki
curl -s http://localhost:9009/ready              # Mimir
curl -s http://localhost:8888/metrics            # OTel Collector
```

### Resource Usage

```bash
# Docker stats
docker stats --no-stream

# Disk usage
df -h
docker system df

# Memory usage
free -h

# Network traffic
iftop -i eth0
```

### Log Aggregation

```bash
# View all logs
docker-compose logs -f

# Specific service
docker-compose logs -f grafana

# Error grep
docker-compose logs | grep ERROR
```

## Performance Tuning

### For High Volume (>1000 agents)

1. **Increase memory limits** in docker-compose.production.yml
2. **Use external object storage** (S3/MinIO)
3. **Enable horizontal scaling** for Mimir
4. **Implement sampling** in OTel Collector
5. **Use dedicated database server** for Grafana

### Storage Optimization

```yaml
# In mimir.yaml
compactor:
  block_ranges: [2h, 12h, 24h]  # Optimize block sizes
  
limits:
  compactor_blocks_retention_period: 30d  # Reduce retention
```

## Security Hardening

### 1. Disable Unnecessary Ports

Only expose what's needed:
- 443 (Grafana via Nginx)
- 4317 (OTLP gRPC for agents)

### 2. Enable Authentication

```yaml
# In grafana.ini
[auth.anonymous]
enabled = false

[auth.basic]
enabled = false

[auth.google]
enabled = true
allowed_domains = ciris.ai
```

### 3. Regular Updates

```bash
# Create update script
cat > /opt/cirislens/update.sh << 'EOF'
#!/bin/bash
cd /opt/cirislens
git pull
docker-compose pull
docker-compose up -d
docker image prune -f
EOF

chmod +x /opt/cirislens/update.sh
```

## Troubleshooting

### No Data Showing

1. Check agent connectivity:
```bash
tcpdump -i any port 4317 -nn
```

2. Check collector logs:
```bash
docker-compose logs otel-collector | tail -100
```

3. Verify Prometheus targets:
```
http://localhost:9090/targets
```

### High Memory Usage

```bash
# Restart memory-heavy services
docker-compose restart mimir
docker-compose restart tempo

# Clear caches
docker system prune -a
```

### Disk Full

```bash
# Find large files
ncdu /var/lib/docker

# Clean up old data
docker volume prune
docker image prune -a
```

## Maintenance Windows

For updates with minimal downtime:

```bash
# 1. Pull new images
docker-compose pull

# 2. Update one service at a time
docker-compose up -d --no-deps grafana
sleep 30
docker-compose up -d --no-deps mimir
sleep 30
docker-compose up -d --no-deps tempo
sleep 30
docker-compose up -d --no-deps loki
```

## Support

- **Logs**: `/var/log/nginx/`, `docker-compose logs`
- **Metrics**: https://lens.ciris.ai/d/cirislens-health
- **SSH**: `ssh -i ~/.ssh/ciris_deploy root@observability.ciris.ai`
- **Issues**: https://github.com/CIRISAI/CIRISLens/issues