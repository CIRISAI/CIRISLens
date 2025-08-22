#!/bin/bash
# CIRISLens Production Deployment Script
# Deploys CIRISLens on agents.ciris.ai

set -e

echo "CIRISLens Production Deployment Script"
echo "======================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root"
    exit 1
fi

# Configuration
DEPLOY_DIR="/opt/cirislens"
DATA_DIR="/data/cirislens"
NGINX_SITES="/etc/nginx/sites-available"
NGINX_ENABLED="/etc/nginx/sites-enabled"

echo "1. Creating directories..."
mkdir -p $DEPLOY_DIR
mkdir -p $DATA_DIR/{postgres,grafana,prometheus,loki,tempo,mimir,minio}
chmod 755 $DATA_DIR

echo "2. Cloning repository..."
if [ ! -d "$DEPLOY_DIR/.git" ]; then
    git clone https://github.com/CIRISAI/CIRISLens.git $DEPLOY_DIR
else
    cd $DEPLOY_DIR
    git pull origin main
fi

cd $DEPLOY_DIR

echo "3. Setting up environment..."
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << 'EOF'
# Production Configuration
ENV=production
DATABASE_URL=postgresql://cirislens:$(openssl rand -hex 16)@cirislens-db:5432/cirislens

# OAuth Configuration
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-your-client-id}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-your-secret}
OAUTH_CALLBACK_URL=https://agents.ciris.ai/lens/admin/auth/callback
ALLOWED_DOMAIN=ciris.ai
SESSION_SECRET=$(openssl rand -hex 32)

# Collection Settings
COLLECTION_INTERVAL_SECONDS=30
OTLP_COLLECTION_ENABLED=true

# Grafana Admin (IMPORTANT: Change this!)
GF_ADMIN_USER=admin
GF_ADMIN_PASSWORD=$(openssl rand -hex 16)
GF_ADMIN_EMAIL=admin@ciris.ai
GF_SECRET_KEY=$(openssl rand -hex 32)

# MinIO
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
EOF
    echo "Please edit .env and add your Google OAuth credentials and agent tokens"
    echo "Press enter to continue after editing..."
    read
fi

echo "4. Installing nginx configuration..."
if [ -f "nginx/lens-site.conf" ]; then
    cp nginx/lens-site.conf $NGINX_SITES/cirislens.conf
    ln -sf $NGINX_SITES/cirislens.conf $NGINX_ENABLED/cirislens.conf
    echo "Testing nginx configuration..."
    nginx -t
    if [ $? -eq 0 ]; then
        echo "Reloading nginx..."
        nginx -s reload
    else
        echo "Nginx configuration error! Please check manually."
        exit 1
    fi
fi

echo "5. Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "Docker not found! Please install Docker first."
    exit 1
fi

if ! docker network ls | grep -q "ciris-network"; then
    echo "Warning: ciris-network not found. Creating it..."
    docker network create ciris-network
fi

echo "6. Starting CIRISLens stack..."
docker compose -f docker-compose.managed.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.managed.yml -f docker-compose.prod.yml up -d

echo "7. Waiting for services to start..."
sleep 10

echo "8. Checking service health..."
docker compose -f docker-compose.managed.yml -f docker-compose.prod.yml ps

echo "9. Setting up Grafana for public access..."
./scripts/setup-grafana-public.sh

echo "10. Testing endpoints..."
echo -n "API Health: "
curl -s http://localhost:8200/health | jq -r '.status' || echo "Failed"

echo -n "Grafana Health: "
curl -s http://localhost:3001/api/health | jq -r '.database' || echo "Failed"

echo ""
echo "Deployment Complete!"
echo "===================="
echo ""
echo "Access URLs:"
echo "- Public Dashboards: https://agents.ciris.ai/lens/"
echo "- Admin Interface: https://agents.ciris.ai/lens/admin/"
echo "- API Health: https://agents.ciris.ai/lens/api/health"
echo ""
echo "Grafana Admin Credentials (saved in .env):"
grep "GF_ADMIN_USER\|GF_ADMIN_PASSWORD" .env | grep -v SECRET_KEY
echo ""
echo "Next Steps:"
echo "1. Access admin UI and configure agent tokens"
echo "2. Verify telemetry collection is working"
echo "3. Configure Grafana dashboards"
echo "4. Set up backup cron job: crontab -e"
echo "   0 2 * * * /opt/cirislens/scripts/backup.sh"
echo ""
echo "To view logs: docker compose -f docker-compose.managed.yml -f docker-compose.prod.yml logs -f"