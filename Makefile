# CIRISLens Development Makefile

.PHONY: help dev-setup dev-up dev-down dev-logs clean test build

# Default target
help:
	@echo "CIRISLens Development Commands:"
	@echo ""
	@echo "  make dev-setup    - Initial development setup (certs, configs)"
	@echo "  make dev-up       - Start all services with mock nginx"
	@echo "  make dev-down     - Stop all services"
	@echo "  make dev-logs     - Follow container logs"
	@echo "  make dev-restart  - Restart all services"
	@echo "  make clean        - Clean up volumes and containers"
	@echo "  make test         - Run tests"
	@echo "  make build        - Build production images"
	@echo ""
	@echo "Quick Start:"
	@echo "  1. make dev-setup"
	@echo "  2. make dev-up"
	@echo "  3. Open http://localhost:8080"

# Development setup
dev-setup:
	@echo "🔧 Setting up development environment..."
	@echo ""
	@echo "📜 Generating self-signed certificates..."
	@cd nginx && ./generate-dev-certs.sh
	@echo ""
	@echo "📁 Creating required directories..."
	@mkdir -p config/grafana/provisioning/datasources
	@mkdir -p config/grafana/provisioning/dashboards
	@mkdir -p dashboards
	@mkdir -p nginx/sites-enabled
	@echo ""
	@echo "✅ Development setup complete!"

# Start development environment with mock nginx
dev-up: dev-setup
	@echo "🚀 Starting CIRISLens with mock managed nginx..."
	docker compose -f docker-compose.managed.yml up -d
	@echo ""
	@echo "⏳ Waiting for services to be ready..."
	@sleep 10
	@echo ""
	@echo "✅ CIRISLens is running!"
	@echo ""
	@echo "📊 Access Points:"
	@echo "  - Main Portal:     http://localhost:8080"
	@echo "  - Public Dashboard: http://localhost:8080/cirislens/public/"
	@echo "  - Admin Interface: http://localhost:8080/cirislens/admin/"
	@echo "  - Grafana Direct:  http://localhost:3000 (admin/admin)"
	@echo "  - Prometheus:      http://localhost:9090"
	@echo "  - MinIO Console:   http://localhost:9001 (admin/adminpassword123)"
	@echo ""
	@echo "⚠️  Note: Admin interface uses mock auth in dev mode (dev@ciris.ai)"

# Stop development environment
dev-down:
	@echo "🛑 Stopping CIRISLens services..."
	docker compose -f docker-compose.managed.yml down
	@echo "✅ Services stopped"

# View logs
dev-logs:
	docker compose -f docker-compose.managed.yml logs -f

# Restart services
dev-restart: dev-down dev-up

# Clean up everything
clean:
	@echo "🧹 Cleaning up CIRISLens..."
	docker compose -f docker-compose.managed.yml down -v
	@rm -rf nginx/ssl/*.pem
	@echo "✅ Cleanup complete"

# Run tests
test:
	@echo "🧪 Running tests..."
	@echo "TODO: Add test suite"

# Build production images
build:
	@echo "🏗️ Building production images..."
	docker build -t cirislens-api:latest ./api
	@echo "✅ Build complete"

# Check service health
health-check:
	@echo "🏥 Checking service health..."
	@curl -s http://localhost:8080/health > /dev/null && echo "✅ Nginx: Healthy" || echo "❌ Nginx: Unhealthy"
	@curl -s http://localhost:8000/health > /dev/null && echo "✅ API: Healthy" || echo "❌ API: Unhealthy"
	@curl -s http://localhost:3000/api/health > /dev/null && echo "✅ Grafana: Healthy" || echo "❌ Grafana: Unhealthy"
	@curl -s http://localhost:9090/-/healthy > /dev/null && echo "✅ Prometheus: Healthy" || echo "❌ Prometheus: Unhealthy"
	@curl -s http://localhost:3100/ready > /dev/null && echo "✅ Loki: Healthy" || echo "❌ Loki: Unhealthy"
	@curl -s http://localhost:3200/ready > /dev/null && echo "✅ Tempo: Healthy" || echo "❌ Tempo: Unhealthy"
	@curl -s http://localhost:9000/minio/health/live > /dev/null && echo "✅ MinIO: Healthy" || echo "❌ MinIO: Unhealthy"

# Show running containers
ps:
	@docker compose -f docker-compose.managed.yml ps

# Access API shell
api-shell:
	docker compose -f docker-compose.managed.yml exec cirislens-api /bin/bash

# Access database
db-shell:
	docker compose -f docker-compose.managed.yml exec cirislens-db psql -U cirislens -d cirislens

# View nginx config
nginx-config:
	docker compose -f docker-compose.managed.yml exec mock-managed-nginx cat /etc/nginx/nginx.conf

# Tail nginx access logs
nginx-logs:
	docker compose -f docker-compose.managed.yml exec mock-managed-nginx tail -f /var/log/nginx/access.log