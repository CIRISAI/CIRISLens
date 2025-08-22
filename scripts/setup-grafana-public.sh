#!/bin/bash
# Setup Grafana for public dashboard access
# Run this after Grafana starts for the first time

set -e

echo "Setting up Grafana for public access..."

GRAFANA_URL="http://localhost:3001"
ADMIN_USER="admin"
ADMIN_PASS="admin"

# Wait for Grafana to be ready
echo "Waiting for Grafana to start..."
for i in {1..30}; do
    if curl -s "$GRAFANA_URL/api/health" > /dev/null; then
        echo "Grafana is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 2
done

# Create or update the Public organization
echo "Setting up Public organization..."
curl -X POST "$GRAFANA_URL/api/orgs" \
    -H "Content-Type: application/json" \
    -u "$ADMIN_USER:$ADMIN_PASS" \
    -d '{
        "name": "Public"
    }' 2>/dev/null || echo "Organization might already exist"

# Get the Public org ID
PUBLIC_ORG_ID=$(curl -s "$GRAFANA_URL/api/orgs/name/Public" \
    -u "$ADMIN_USER:$ADMIN_PASS" | jq -r '.id')

echo "Public Org ID: $PUBLIC_ORG_ID"

# Update org preferences to set home dashboard
if [ ! -z "$PUBLIC_ORG_ID" ]; then
    echo "Setting default dashboard for Public org..."
    curl -X PUT "$GRAFANA_URL/api/org/preferences" \
        -H "Content-Type: application/json" \
        -H "X-Grafana-Org-Id: $PUBLIC_ORG_ID" \
        -u "$ADMIN_USER:$ADMIN_PASS" \
        -d '{
            "theme": "light",
            "homeDashboardId": 0,
            "timezone": "browser"
        }'
fi

# Make dashboards publicly accessible
echo "Configuring public dashboard access..."

# Create an API key for automated operations (optional)
API_KEY=$(curl -X POST "$GRAFANA_URL/api/auth/keys" \
    -H "Content-Type: application/json" \
    -u "$ADMIN_USER:$ADMIN_PASS" \
    -d '{
        "name": "cirislens-public",
        "role": "Viewer"
    }' 2>/dev/null | jq -r '.key')

if [ ! -z "$API_KEY" ]; then
    echo "API Key created for public access"
fi

echo ""
echo "Grafana public access setup complete!"
echo "======================================="
echo "Public URL: https://agents.ciris.ai/lens/"
echo "Admin URL: https://agents.ciris.ai/lens/login"
echo ""
echo "Note: Anonymous users will automatically see public dashboards"
echo "Admin users can still login to edit dashboards"