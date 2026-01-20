"""
CIRISLens API Service
Mock implementation for development
"""

import asyncio
import json
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

# Import Covenant API router
from covenant_api import router as covenant_router
from log_ingest import LogIngestService
from manager_collector import ManagerCollector
from otlp_collector import OTLPCollector
from token_manager import TokenManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable docs in production
IS_PRODUCTION = os.getenv("ENV", "").lower() == "production"

# Initialize FastAPI app
app = FastAPI(
    title="CIRISLens API",
    description="Telemetry and Observability Platform for CIRIS",
    version="0.1.0-dev",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# CORS configuration
CORS_ORIGINS = [
    "http://localhost:8080",
    "http://localhost:3000",
    "https://agents.ciris.ai",
    "https://ciris.ai",
    "https://www.ciris.ai",
    "https://lens.ciris-services-1.ai",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include Covenant API router for CIRIS Covenant 1.0b compliance
app.include_router(covenant_router)


# Configuration from environment
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "mock-client-id")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "mock-secret")
OAUTH_CALLBACK_URL = os.getenv(
    "OAUTH_CALLBACK_URL", "http://localhost:8080/lens/backend/admin/auth/callback"
)
MANAGER_API_URL = os.getenv("MANAGER_API_URL", "http://host.docker.internal:8888/manager/v1")
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "ciris.ai")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")

# In-memory storage for development
sessions = {}
telemetry_configs = {}
visibility_configs = {}

# Database configuration - DATABASE_URL env var is required in production
DATABASE_URL = os.getenv("DATABASE_URL", "")
db_pool = None
manager_collector = None
otlp_collector = None
log_ingest_service = None

# SQL Constants
SQL_INSERT_STATUS_CHECK = """
    INSERT INTO cirislens.status_checks
    (service_name, provider_name, region, status, latency_ms, error_message)
    VALUES ($1, $2, $3, $4, $5, $6)
"""

# Error message constants
ERR_DATABASE_NOT_AVAILABLE = "Database not available"
ERR_LOG_INGEST_NOT_AVAILABLE = "Log ingestion service not available"

token_manager = TokenManager()


# Models
class OAuthUser(BaseModel):
    email: EmailStr
    name: str
    picture: str | None = None
    hd: str | None = None


class TelemetryConfig(BaseModel):
    agent_id: str
    enabled: bool = False
    collection_interval: int = 60
    metrics_enabled: bool = True
    traces_enabled: bool = True
    logs_enabled: bool = True
    last_updated: str = ""
    updated_by: str = ""


class VisibilityConfig(BaseModel):
    agent_id: str
    public_visible: bool = False
    show_metrics: bool = True
    show_traces: bool = False
    show_logs: bool = False
    show_cognitive_state: bool = True
    show_health_status: bool = True
    redact_pii: bool = True
    last_updated: str = ""
    updated_by: str = ""


class ManagerConfig(BaseModel):
    name: str
    url: str
    description: str | None = ""
    auth_token: str | None = None
    collection_interval_seconds: int = 30
    enabled: bool = True


class ManagerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    auth_token: str | None = None
    collection_interval_seconds: int | None = None
    enabled: bool | None = None


class AgentTokenConfig(BaseModel):
    agent_name: str
    token: str
    url: str


# Session management
def create_session(user: OAuthUser) -> str:
    """Create a new session for authenticated user"""
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "user": user.dict(),
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    }
    return session_id


def get_current_user(request: Request) -> dict | None:
    """Get current user from session"""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        return None

    session = sessions[session_id]
    if datetime.fromisoformat(session["expires_at"]) < datetime.now(UTC):
        del sessions[session_id]
        return None

    return session["user"]


def require_auth(request: Request) -> dict:
    """Dependency to require authentication"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def run_manager_collector():
    """Wrapper to run manager collector with exception handling"""
    try:
        await manager_collector.start()
    except Exception as e:
        logger.error(f"CRITICAL: Manager collector failed to start: {e}", exc_info=True)
        raise


async def run_otlp_collector():
    """Wrapper to run OTLP collector with exception handling"""
    try:
        await otlp_collector.start()
    except Exception as e:
        logger.error(f"CRITICAL: OTLP collector failed to start: {e}", exc_info=True)
        raise


async def run_status_collector():  # noqa: PLR0912
    """Background task to collect and store status checks every 60 seconds"""
    logger.info("Status collector started")

    # Multi-region service URLs from environment
    regions = {
        "us": {
            "billing": os.getenv("US_BILLING_URL", ""),
            "proxy": os.getenv("US_PROXY_URL", ""),
        },
        "eu": {
            "billing": os.getenv("EU_BILLING_URL", ""),
            "proxy": os.getenv("EU_PROXY_URL", ""),
        },
    }

    while True:
        try:
            # Collect status from local CIRISLens providers (global)
            pg_status, grafana_status = await asyncio.gather(
                check_postgresql(),
                check_grafana(),
                return_exceptions=True
            )

            # Collect status from all regional services
            regional_tasks = []
            for region, services in regions.items():
                for service, url in services.items():
                    if url:
                        regional_tasks.append((region, service, fetch_service_status(service, url)))

            # Gather all regional results
            regional_results = []
            for region, service, task in regional_tasks:
                result = await task
                regional_results.append((region, service, result))

            # Store results in database
            if db_pool:
                async with db_pool.acquire() as conn:
                    # Store CIRISLens local checks (global region)
                    if isinstance(pg_status, ProviderStatus):
                        await conn.execute(
                            SQL_INSERT_STATUS_CHECK, "cirislens", "postgresql", "global",
                            pg_status.status, pg_status.latency_ms, pg_status.message
                        )

                    if isinstance(grafana_status, ProviderStatus):
                        await conn.execute(
                            SQL_INSERT_STATUS_CHECK, "cirislens", "grafana", "global",
                            grafana_status.status, grafana_status.latency_ms, grafana_status.message
                        )

                    # Store regional service checks
                    for region, service, result in regional_results:
                        if not isinstance(result, tuple):
                            continue

                        _name, service_data = result

                        # Store overall service status
                        await conn.execute(
                            SQL_INSERT_STATUS_CHECK, f"ciris{service}", "service", region,
                            service_data.get("status", "unknown"), None, service_data.get("error")
                        )

                        if "providers" not in service_data:
                            continue

                        providers = service_data["providers"]

                        # Handle dict format (CIRISBilling)
                        if isinstance(providers, dict):
                            for provider, pdata in providers.items():
                                # LLM providers are global, others are regional
                                prov_region = "global" if provider in ["openrouter", "groq", "together", "openai"] else region
                                await conn.execute(
                                    SQL_INSERT_STATUS_CHECK, f"ciris{service}", provider, prov_region,
                                    pdata.get("status", "unknown"), pdata.get("latency_ms"), pdata.get("message")
                                )

                        # Handle array format (CIRISProxy)
                        elif isinstance(providers, list):
                            for pdata in providers:
                                provider = pdata.get("provider", "unknown")
                                # LLM providers are global, others are regional
                                prov_region = "global" if provider in ["openrouter", "groq", "together", "openai"] else region
                                await conn.execute(
                                    SQL_INSERT_STATUS_CHECK, f"ciris{service}", provider, prov_region,
                                    pdata.get("status", "unknown"), pdata.get("latency_ms"), pdata.get("error")
                                )

                logger.debug("Status checks recorded")

        except Exception as e:
            logger.error(f"Status collector error: {e}")

        # Wait 60 seconds before next check
        await asyncio.sleep(60)


# Startup and shutdown events
@app.on_event("startup")
async def startup():
    """Initialize database and start collectors"""
    global db_pool, manager_collector, otlp_collector, log_ingest_service

    try:
        # Create database pool
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        logger.info("Database pool created")

        # Initialize tables
        async with db_pool.acquire() as conn:
            # Create manager tables (required for manager collector)
            try:
                sql_content = Path("/app/sql/manager_tables.sql").read_text()
                await conn.execute(sql_content)
                logger.info("Manager tables initialized")
            except Exception as e:
                logger.warning(f"Manager tables migration may have already run: {e}")
            # Create OTLP tables only if OTLP collection is enabled
            if os.getenv("OTLP_COLLECTION_ENABLED", "true").lower() == "true":
                sql_content = Path("/app/sql/otlp_tables.sql").read_text()
                await conn.execute(sql_content)
            # Create service logs tables
            try:
                sql_content = Path("/app/sql/007_service_logs.sql").read_text()
                await conn.execute(sql_content)
                logger.info("Service logs tables initialized")
            except Exception as e:
                logger.warning(f"Service logs migration may have already run: {e}")
            # Create status checks tables
            try:
                sql_content = Path("/app/sql/008_status_checks.sql").read_text()
                await conn.execute(sql_content)
                logger.info("Status checks tables initialized")
            except Exception as e:
                logger.warning(f"Status checks migration may have already run: {e}")
            logger.info("Database tables initialized")

        # Initialize log ingest service
        log_ingest_service = LogIngestService(db_pool)
        logger.info("Log ingest service initialized")

        # Start manager collector with shared pool
        manager_collector = ManagerCollector(DATABASE_URL, pool=db_pool)
        task = asyncio.create_task(run_manager_collector())
        task.add_done_callback(
            lambda t: logger.error(f"Manager collector task ended: {t.exception()}")
            if t.exception()
            else None
        )
        logger.info("Manager collector task created")

        # Start OTLP collector if enabled
        if os.getenv("OTLP_COLLECTION_ENABLED", "true").lower() == "true":
            otlp_collector = OTLPCollector(DATABASE_URL)
            task = asyncio.create_task(run_otlp_collector())
            task.add_done_callback(
                lambda t: logger.error(f"OTLP collector task ended: {t.exception()}")
                if t.exception()
                else None
            )
            logger.info("OTLP collector task created")

        # Start status collector for availability monitoring
        task = asyncio.create_task(run_status_collector())
        task.add_done_callback(
            lambda t: logger.error(f"Status collector task ended: {t.exception()}")
            if t.exception()
            else None
        )
        logger.info("Status collector task created")

    except Exception as e:
        logger.error(f"Startup error: {e}", exc_info=True)
        # Continue anyway for development


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    global db_pool, manager_collector, otlp_collector

    if manager_collector:
        await manager_collector.stop()

    if otlp_collector:
        await otlp_collector.stop()

    if db_pool:
        await db_pool.close()


# Routes
@app.get("/")
async def root():
    """Root endpoint"""
    return {"service": "CIRISLens API", "version": "0.1.0-dev", "status": "online"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}


# Status check models
class ProviderStatus(BaseModel):
    status: str  # operational, degraded, outage
    latency_ms: int | None = None
    last_check: str
    message: str | None = None


class ServiceStatus(BaseModel):
    service: str
    status: str
    timestamp: str
    version: str
    providers: dict[str, ProviderStatus]


async def check_postgresql() -> ProviderStatus:
    """Check PostgreSQL connectivity and latency"""
    start = datetime.now(UTC)
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5.0)
            latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
            status = "operational" if latency < 1000 else "degraded"
            return ProviderStatus(
                status=status,
                latency_ms=latency,
                last_check=datetime.now(UTC).isoformat() + "Z"
            )
        else:
            return ProviderStatus(
                status="outage",
                latency_ms=None,
                last_check=datetime.now(UTC).isoformat() + "Z",
                message="Database pool not initialized"
            )
    except TimeoutError:
        return ProviderStatus(
            status="outage",
            latency_ms=5000,
            last_check=datetime.now(UTC).isoformat() + "Z",
            message="Connection timeout"
        )
    except Exception as e:
        return ProviderStatus(
            status="outage",
            latency_ms=None,
            last_check=datetime.now(UTC).isoformat() + "Z",
            message=str(e)[:100]
        )


async def check_grafana() -> ProviderStatus:
    """Check Grafana health endpoint"""
    grafana_url = os.getenv("GRAFANA_URL", "http://grafana:3000")
    start = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{grafana_url}/api/health")
            latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
            if response.status_code == 200:
                status = "operational" if latency < 1000 else "degraded"
                return ProviderStatus(
                    status=status,
                    latency_ms=latency,
                    last_check=datetime.now(UTC).isoformat() + "Z"
                )
            else:
                return ProviderStatus(
                    status="degraded",
                    latency_ms=latency,
                    last_check=datetime.now(UTC).isoformat() + "Z",
                    message=f"HTTP {response.status_code}"
                )
    except httpx.TimeoutException:
        return ProviderStatus(
            status="outage",
            latency_ms=5000,
            last_check=datetime.now(UTC).isoformat() + "Z",
            message="Connection timeout"
        )
    except Exception as e:
        return ProviderStatus(
            status="outage",
            latency_ms=None,
            last_check=datetime.now(UTC).isoformat() + "Z",
            message=str(e)[:100]
        )


@app.get("/v1/status", response_model=ServiceStatus)
async def service_status():
    """Public status endpoint for CIRISLens service health"""
    # Check all providers in parallel
    pg_status, grafana_status = await asyncio.gather(
        check_postgresql(),
        check_grafana()
    )

    # Determine overall status
    statuses = [pg_status.status, grafana_status.status]
    if "outage" in statuses:
        overall = "outage"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "operational"

    return ServiceStatus(
        service="cirislens",
        status=overall,
        timestamp=datetime.now(UTC).isoformat() + "Z",
        version=app.version,
        providers={
            "postgresql": pg_status,
            "grafana": grafana_status
        }
    )


# Aggregated status models
class ServiceSummary(BaseModel):
    name: str
    status: str
    latency_ms: int | None = None


class RegionStatus(BaseModel):
    name: str
    status: str
    services: dict[str, ServiceSummary]


class InfrastructureStatus(BaseModel):
    name: str
    status: str
    provider: str
    latency_ms: int | None = None


class ProviderDetail(BaseModel):
    status: str
    latency_ms: int | None = None
    source: str | None = None  # Which service reported this


class AggregatedStatus(BaseModel):
    status: str
    timestamp: str
    last_incident: str | None = None
    regions: dict[str, RegionStatus]
    infrastructure: dict[str, InfrastructureStatus]
    llm_providers: dict[str, ProviderDetail]
    auth_providers: dict[str, ProviderDetail]
    database_providers: dict[str, ProviderDetail]
    internal_providers: dict[str, ProviderDetail]


async def fetch_service_status(name: str, url: str) -> tuple[str, dict]:
    """Fetch status from a CIRIS service"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/v1/status")
            if response.status_code == 200:
                data = response.json()
                return name, data
            else:
                return name, {"status": "degraded", "error": f"HTTP {response.status_code}"}
    except httpx.TimeoutException:
        return name, {"status": "outage", "error": "Timeout"}
    except Exception:
        # Don't leak internal error details
        return name, {"status": "outage", "error": "Connection failed"}


async def check_infrastructure(
    name: str, url: str, provider: str, latency_threshold: int = 1000, accept_401: bool = False
) -> InfrastructureStatus:
    """Check infrastructure endpoint availability"""
    start = datetime.now(UTC)
    try:
        # Limit redirects to prevent SSRF via open redirect
        async with httpx.AsyncClient(timeout=5.0, max_redirects=3) as client:
            response = await client.get(url, follow_redirects=True)
            latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
            # Accept 401 for endpoints that require auth (proves they're responding)
            ok_status = response.status_code < 400 or (accept_401 and response.status_code == 401)
            status = "operational" if ok_status and latency < latency_threshold else "degraded"
            return InfrastructureStatus(name=name, status=status, provider=provider, latency_ms=latency)
    except Exception:
        return InfrastructureStatus(name=name, status="outage", provider=provider, latency_ms=None)


async def check_external_provider(
    name: str,
    url: str,
    api_key: str | None = None,
    api_key_header: str = "x-api-key",
    expected_text: str | None = None,
    latency_threshold: int = 2000,
) -> ProviderDetail:
    """
    Check external provider health endpoint.

    Args:
        name: Provider name for logging
        url: Health check URL
        api_key: Optional API key for authenticated endpoints
        api_key_header: Header name for API key (default: x-api-key)
        expected_text: Optional text to check in response body
        latency_threshold: Max acceptable latency in ms
    """
    start = datetime.now(UTC)
    try:
        headers = {}
        if api_key:
            headers[api_key_header] = api_key

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            latency = int((datetime.now(UTC) - start).total_seconds() * 1000)

            if response.status_code == 200:
                # Optionally check response body
                if expected_text and expected_text not in response.text:
                    return ProviderDetail(
                        status="degraded",
                        latency_ms=latency,
                        source=f"direct.{name}"
                    )
                status = "operational" if latency < latency_threshold else "degraded"
                return ProviderDetail(status=status, latency_ms=latency, source=f"direct.{name}")
            else:
                return ProviderDetail(
                    status="degraded",
                    latency_ms=latency,
                    source=f"direct.{name}"
                )
    except httpx.TimeoutException:
        return ProviderDetail(status="outage", latency_ms=None, source=f"direct.{name}")
    except Exception:
        return ProviderDetail(status="outage", latency_ms=None, source=f"direct.{name}")


@app.get("/api/v1/status", response_model=AggregatedStatus)
async def aggregated_status():  # noqa: PLR0912
    """
    Public aggregated status endpoint for ciris.ai/status page.
    Fetches status from all CIRIS services across all regions.
    """
    # Multi-region service URLs from environment
    region_configs = {
        "us": {
            "name": "US (Chicago)",
            "billing": os.getenv("US_BILLING_URL", ""),
            "proxy": os.getenv("US_PROXY_URL", ""),
            "health": os.getenv("VULTR_HEALTH_URL", ""),
        },
        "eu": {
            "name": "EU (Germany)",
            "billing": os.getenv("EU_BILLING_URL", ""),
            "proxy": os.getenv("EU_PROXY_URL", ""),
            "health": os.getenv("HETZNER_HEALTH_URL", ""),
        },
    }

    # External provider health check URLs (configurable via env vars)
    external_providers_config = {
        "exa": {
            "url": os.getenv("EXA_HEALTH_URL", ""),
            "api_key": os.getenv("EXA_API_KEY", ""),
            "api_key_header": "x-api-key",
            "expected_text": "healthy",  # Exa returns "I am healthy."
            "display_name": "web_search",
        },
        # Add more external providers here as needed
        # "brave": {
        #     "url": os.getenv("BRAVE_HEALTH_URL", ""),
        #     "api_key": os.getenv("BRAVE_API_KEY", ""),
        #     ...
        # },
    }

    # Infrastructure checks
    ghcr_health_url = os.getenv("GHCR_HEALTH_URL", "https://ghcr.io/v2/")

    # Fetch all regional service statuses in parallel
    regional_tasks = []
    for region, config in region_configs.items():
        if config["billing"]:
            regional_tasks.append((region, "billing", fetch_service_status("billing", config["billing"])))
        if config["proxy"]:
            regional_tasks.append((region, "proxy", fetch_service_status("proxy", config["proxy"])))

    # Gather regional results
    regional_results: dict[str, dict[str, Any]] = {"us": {}, "eu": {}}
    for region, service, task in regional_tasks:
        result = await task
        if isinstance(result, tuple):
            regional_results[region][service] = result[1]

    # Infrastructure checks
    infra_tasks = []
    for region, config in region_configs.items():
        if config["health"]:
            provider = "vultr" if region == "us" else "hetzner"
            infra_tasks.append(
                check_infrastructure(config["name"], config["health"], provider)
            )
    if ghcr_health_url:
        infra_tasks.append(
            check_infrastructure(
                "Container Registry", ghcr_health_url, "github",
                latency_threshold=3000, accept_401=True
            )
        )

    infra_results = await asyncio.gather(*infra_tasks, return_exceptions=True)

    # Direct external provider health checks (run in parallel)
    external_provider_results: dict[str, ProviderDetail] = {}
    external_tasks = []
    for provider_name, config in external_providers_config.items():
        if config["url"]:
            external_tasks.append(
                (
                    config.get("display_name", provider_name),
                    check_external_provider(
                        name=provider_name,
                        url=config["url"],
                        api_key=config.get("api_key") or None,
                        api_key_header=config.get("api_key_header", "x-api-key"),
                        expected_text=config.get("expected_text"),
                    )
                )
            )

    if external_tasks:
        for display_name, task in external_tasks:
            result = await task
            if isinstance(result, ProviderDetail):
                external_provider_results[display_name] = result

    # Build regions dict
    regions: dict[str, RegionStatus] = {}
    for region, config in region_configs.items():
        services: dict[str, ServiceSummary] = {}
        region_data = regional_results.get(region, {})

        if "billing" in region_data:
            services["billing"] = ServiceSummary(
                name="Billing & Authentication",
                status=region_data["billing"].get("status", "unknown"),
                latency_ms=None
            )
        if "proxy" in region_data:
            # Calculate proxy status based on LLM providers
            # Only degraded if ALL providers are degraded or worse
            proxy_status = "operational"
            proxy_providers = region_data["proxy"].get("providers", [])
            if isinstance(proxy_providers, list) and proxy_providers:
                llm_statuses = [
                    p.get("status", "unknown")
                    for p in proxy_providers
                    if p.get("provider") in ["openrouter", "groq", "together", "openai"]
                ]
                if llm_statuses:
                    # Only degraded/outage if ALL LLM providers are degraded or worse
                    if all(s == "outage" for s in llm_statuses):
                        proxy_status = "outage"
                    elif all(s in ["degraded", "outage"] for s in llm_statuses):
                        proxy_status = "degraded"
            services["proxy"] = ServiceSummary(
                name="LLM Proxy",
                status=proxy_status,
                latency_ms=None
            )

        # Calculate region status
        if services:
            statuses = [s.status for s in services.values()]
            if "outage" in statuses:
                region_status = "outage"
            elif "degraded" in statuses:
                region_status = "degraded"
            else:
                region_status = "operational"
        else:
            region_status = "unknown"

        regions[region] = RegionStatus(
            name=config["name"],
            status=region_status,
            services=services
        )

    # Build infrastructure dict
    infrastructure = {}
    for result in infra_results:
        if isinstance(result, InfrastructureStatus):
            infrastructure[result.provider] = result

    # Extract provider statuses (from first available region)
    llm_providers: dict[str, ProviderDetail] = {}
    auth_providers: dict[str, ProviderDetail] = {}
    database_providers: dict[str, ProviderDetail] = {}
    internal_providers: dict[str, ProviderDetail] = {}

    # Get local CIRISLens status
    lens_status = await service_status()
    database_providers["lens.postgresql"] = ProviderDetail(
        status=lens_status.providers["postgresql"].status,
        latency_ms=lens_status.providers["postgresql"].latency_ms,
        source="cirislens"
    )
    internal_providers["lens.grafana"] = ProviderDetail(
        status=lens_status.providers["grafana"].status,
        latency_ms=lens_status.providers["grafana"].latency_ms,
        source="cirislens"
    )

    # Process regional service data for providers (use first available region)
    for region, region_data in regional_results.items():
        # CIRISBilling providers
        if "billing" in region_data and "providers" in region_data["billing"]:
            providers = region_data["billing"]["providers"]
            if isinstance(providers, dict):
                for provider, pdata in providers.items():
                    detail = ProviderDetail(
                        status=pdata.get("status", "unknown"),
                        latency_ms=pdata.get("latency_ms"),
                        source=f"cirisbilling.{region}"
                    )
                    if provider == "postgresql" and f"{region}.postgresql" not in database_providers:
                        database_providers[f"{region}.postgresql"] = detail
                    elif provider in ["google_oauth", "google_play"] and provider not in auth_providers:
                        auth_providers[provider] = detail

        # CIRISProxy providers
        if "proxy" in region_data and "providers" in region_data["proxy"]:
            providers = region_data["proxy"]["providers"]
            if isinstance(providers, list):
                for pdata in providers:
                    provider = pdata.get("provider", "")
                    detail = ProviderDetail(
                        status=pdata.get("status", "unknown"),
                        latency_ms=pdata.get("latency_ms"),
                        source=f"cirisproxy.{region}"
                    )
                    if provider in ["openrouter", "groq", "together", "openai"] and provider not in llm_providers:
                        llm_providers[provider] = detail
                    elif provider in ["exa", "brave"] and "web_search" not in internal_providers:
                        # Only use proxy-reported status if we don't have a direct check
                        if "web_search" not in external_provider_results:
                            internal_providers["web_search"] = detail

    # Add direct external provider check results (these take precedence)
    for display_name, result in external_provider_results.items():
        internal_providers[display_name] = result

    # Calculate overall status
    all_statuses = [r.status for r in regions.values() if r.status != "unknown"]
    all_statuses.extend([i.status for i in infrastructure.values()])

    if all_statuses.count("outage") >= 3:
        overall = "major_outage"
    elif "outage" in all_statuses:
        overall = "partial_outage"
    elif "degraded" in all_statuses:
        overall = "degraded"
    else:
        overall = "operational"

    return AggregatedStatus(
        status=overall,
        timestamp=datetime.now(UTC).isoformat() + "Z",
        last_incident=None,
        regions=regions,
        infrastructure=infrastructure,
        llm_providers=llm_providers,
        auth_providers=auth_providers,
        database_providers=database_providers,
        internal_providers=internal_providers
    )


@app.get("/api/v1/status/history")
async def status_history(days: int = 30, region: str | None = None):  # noqa: PLR0912
    """
    Get historical uptime data for status page graphs.
    Returns daily uptime percentages for the specified number of days.

    Query parameters:
    - days: Number of days of history (1-365, default 30)
    - region: Filter by region ('us', 'eu', 'global', or omit for all)
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 365")

    valid_regions = {"us", "eu", "global"}
    if region and region not in valid_regions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid region. Must be one of: {', '.join(valid_regions)}"
        )

    try:
        if not db_pool:
            raise HTTPException(status_code=503, detail=ERR_DATABASE_NOT_AVAILABLE)

        async with db_pool.acquire() as conn:
            # Get daily uptime from continuous aggregate
            if region:
                rows = await conn.fetch("""
                    SELECT
                        day::date as date,
                        region,
                        service_name,
                        provider_name,
                        COALESCE(uptime_pct, 100) as uptime_pct,
                        COALESCE(avg_latency_ms, 0) as avg_latency_ms,
                        COALESCE(outage_count, 0) as outage_count
                    FROM cirislens.status_daily
                    WHERE day >= NOW() - INTERVAL '1 day' * $1
                      AND region = $2
                    ORDER BY day DESC, service_name, provider_name
                """, days, region)
            else:
                rows = await conn.fetch("""
                    SELECT
                        day::date as date,
                        region,
                        service_name,
                        provider_name,
                        COALESCE(uptime_pct, 100) as uptime_pct,
                        COALESCE(avg_latency_ms, 0) as avg_latency_ms,
                        COALESCE(outage_count, 0) as outage_count
                    FROM cirislens.status_daily
                    WHERE day >= NOW() - INTERVAL '1 day' * $1
                    ORDER BY day DESC, region, service_name, provider_name
                """, days)

            # Group by date (and optionally region)
            history = {}
            for row in rows:
                date_str = row["date"].isoformat()
                row_region = row["region"]

                if date_str not in history:
                    history[date_str] = {"date": date_str, "regions": {}, "services": {}}

                # Initialize region if needed
                if row_region not in history[date_str]["regions"]:
                    history[date_str]["regions"][row_region] = {"services": {}}

                service = row["service_name"]
                provider = row["provider_name"]
                key = f"{service}.{provider}"

                service_data = {
                    "uptime_pct": float(row["uptime_pct"]),
                    "avg_latency_ms": int(row["avg_latency_ms"]),
                    "outage_count": int(row["outage_count"])
                }

                # Add to region-specific services
                history[date_str]["regions"][row_region]["services"][key] = service_data

                # Also add to flat services dict with region prefix for backwards compat
                history[date_str]["services"][f"{row_region}.{key}"] = service_data

            # Calculate overall uptime per day and per region
            for _date_str, data in history.items():
                # Per-region uptime
                for _region_name, region_data in data["regions"].items():
                    if region_data["services"]:
                        uptimes = [s["uptime_pct"] for s in region_data["services"].values()]
                        region_data["uptime_pct"] = sum(uptimes) / len(uptimes)
                    else:
                        region_data["uptime_pct"] = 100.0

                # Overall uptime across all regions
                if data["services"]:
                    uptimes = [s["uptime_pct"] for s in data["services"].values()]
                    data["overall_uptime_pct"] = sum(uptimes) / len(uptimes)
                else:
                    data["overall_uptime_pct"] = 100.0

            return {
                "days": days,
                "region": region,
                "history": list(history.values())
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching status history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history") from e


# Admin interface routes
@app.get("/admin/")
async def admin_interface(request: Request):
    """Serve admin interface"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/api/admin/auth/login")

    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CIRISLens Admin</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
        }}
        .header {{
            background: #1e293b;
            border-bottom: 1px solid #334155;
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header h1 {{
            font-size: 1.5rem;
            color: #38bdf8;
        }}
        .user-info {{
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
        .user-info span {{ color: #94a3b8; }}
        .logout-btn {{
            background: #475569;
            color: #e2e8f0;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            cursor: pointer;
            text-decoration: none;
        }}
        .logout-btn:hover {{ background: #64748b; }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }}
        .card h2 {{
            color: #38bdf8;
            margin-bottom: 1rem;
            font-size: 1.25rem;
        }}
        .form-row {{
            display: flex;
            gap: 1rem;
            margin-bottom: 1rem;
            flex-wrap: wrap;
        }}
        .form-group {{
            flex: 1;
            min-width: 200px;
        }}
        .form-group label {{
            display: block;
            margin-bottom: 0.5rem;
            color: #94a3b8;
            font-size: 0.875rem;
        }}
        .form-group input, .form-group select {{
            width: 100%;
            padding: 0.75rem;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 6px;
            color: #e2e8f0;
            font-size: 1rem;
        }}
        .form-group input:focus, .form-group select:focus {{
            outline: none;
            border-color: #38bdf8;
        }}
        .btn {{
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1rem;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: #0ea5e9;
            color: white;
        }}
        .btn-primary:hover {{ background: #0284c7; }}
        .btn-danger {{
            background: #dc2626;
            color: white;
        }}
        .btn-danger:hover {{ background: #b91c1c; }}
        .btn-sm {{
            padding: 0.5rem 1rem;
            font-size: 0.875rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            text-align: left;
            padding: 0.75rem;
            border-bottom: 1px solid #334155;
        }}
        th {{
            color: #94a3b8;
            font-weight: 500;
            font-size: 0.875rem;
        }}
        .status-enabled {{ color: #22c55e; }}
        .status-disabled {{ color: #ef4444; }}
        .token-display {{
            background: #0f172a;
            border: 1px solid #22c55e;
            border-radius: 6px;
            padding: 1rem;
            margin: 1rem 0;
            font-family: monospace;
            word-break: break-all;
        }}
        .token-warning {{
            background: #422006;
            border: 1px solid #f59e0b;
            border-radius: 6px;
            padding: 1rem;
            margin: 1rem 0;
            color: #fbbf24;
        }}
        .alert {{
            padding: 1rem;
            border-radius: 6px;
            margin-bottom: 1rem;
        }}
        .alert-success {{
            background: #064e3b;
            border: 1px solid #10b981;
            color: #6ee7b7;
        }}
        .alert-error {{
            background: #450a0a;
            border: 1px solid #dc2626;
            color: #fca5a5;
        }}
        .hidden {{ display: none; }}
        .tabs {{
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1.5rem;
        }}
        .tab {{
            padding: 0.75rem 1.5rem;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 6px 6px 0 0;
            cursor: pointer;
            color: #94a3b8;
        }}
        .tab.active {{
            background: #334155;
            color: #38bdf8;
            border-bottom-color: #334155;
        }}
        .copy-btn {{
            background: #475569;
            color: #e2e8f0;
            border: none;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75rem;
            margin-left: 0.5rem;
        }}
        .copy-btn:hover {{ background: #64748b; }}
        .empty-state {{
            text-align: center;
            padding: 3rem;
            color: #64748b;
        }}
        .loading {{
            text-align: center;
            padding: 2rem;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <header class="header">
        <h1>CIRISLens Admin</h1>
        <div class="user-info">
            <span>{user['email']}</span>
            <a href="#" onclick="logout()" class="logout-btn">Logout</a>
        </div>
    </header>

    <div class="container">
        <div class="tabs">
            <div class="tab active" onclick="showTab('tokens')">Service Tokens</div>
            <div class="tab" onclick="showTab('logs')">Service Logs</div>
        </div>

        <div id="alert-container"></div>

        <!-- Service Tokens Tab -->
        <div id="tokens-tab">
            <div class="card">
                <h2>Create Service Token</h2>
                <p style="color: #64748b; margin-bottom: 1rem;">
                    Generate tokens for CIRISBilling, CIRISProxy, and CIRISManager to send logs.
                </p>
                <form id="create-token-form" onsubmit="createToken(event)">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="service_name">Service Name</label>
                            <select id="service_name" required>
                                <option value="">Select a service...</option>
                                <option value="cirisbilling">CIRISBilling</option>
                                <option value="cirisproxy">CIRISProxy</option>
                                <option value="cirismanager">CIRISManager</option>
                                <option value="custom">Custom...</option>
                            </select>
                        </div>
                        <div class="form-group" id="custom-name-group" style="display: none;">
                            <label for="custom_name">Custom Service Name</label>
                            <input type="text" id="custom_name" placeholder="my-service">
                        </div>
                        <div class="form-group">
                            <label for="description">Description (optional)</label>
                            <input type="text" id="description" placeholder="Production billing service">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Generate Token</button>
                </form>

                <div id="new-token-display" class="hidden">
                    <div class="token-warning">
                        <strong>Save this token now!</strong> It will not be shown again.
                    </div>
                    <div class="token-display">
                        <span id="new-token-value"></span>
                        <button class="copy-btn" onclick="copyToken()">Copy</button>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Active Service Tokens</h2>
                <div id="tokens-loading" class="loading">Loading tokens...</div>
                <div id="tokens-empty" class="empty-state hidden">
                    No service tokens configured yet.
                </div>
                <table id="tokens-table" class="hidden">
                    <thead>
                        <tr>
                            <th>Service</th>
                            <th>Description</th>
                            <th>Created</th>
                            <th>Last Used</th>
                            <th>Status</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="tokens-body"></tbody>
                </table>
            </div>
        </div>

        <!-- Service Logs Tab -->
        <div id="logs-tab" class="hidden">
            <div class="card">
                <h2>Filter Logs</h2>
                <form id="filter-logs-form" onsubmit="filterLogs(event)">
                    <div class="form-row">
                        <div class="form-group">
                            <label for="log_service">Service</label>
                            <select id="log_service">
                                <option value="">All Services</option>
                                <option value="cirisbilling">CIRISBilling</option>
                                <option value="cirisproxy">CIRISProxy</option>
                                <option value="cirismanager">CIRISManager</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="log_level">Level</label>
                            <select id="log_level">
                                <option value="">All Levels</option>
                                <option value="ERROR">ERROR</option>
                                <option value="WARNING">WARNING</option>
                                <option value="INFO">INFO</option>
                                <option value="DEBUG">DEBUG</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="log_limit">Limit</label>
                            <input type="number" id="log_limit" value="100" min="10" max="1000">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">Search</button>
                </form>
            </div>

            <div class="card">
                <h2>Recent Logs</h2>
                <div id="logs-loading" class="loading hidden">Loading logs...</div>
                <div id="logs-empty" class="empty-state">
                    No logs found. Click Search to load logs.
                </div>
                <table id="logs-table" class="hidden">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Service</th>
                            <th>Level</th>
                            <th>Event</th>
                            <th>Message</th>
                        </tr>
                    </thead>
                    <tbody id="logs-body"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // nginx maps /lens/backend/ -> /api/, so we call /lens/backend/admin/ -> /api/admin/
        const API_BASE = '/lens/backend';

        // Tab switching
        function showTab(tab) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelector(`[onclick="showTab('${{tab}}')"]`).classList.add('active');

            document.getElementById('tokens-tab').classList.toggle('hidden', tab !== 'tokens');
            document.getElementById('logs-tab').classList.toggle('hidden', tab !== 'logs');
        }}

        // Alert display
        function showAlert(message, type) {{
            const container = document.getElementById('alert-container');
            container.innerHTML = `<div class="alert alert-${{type}}">${{message}}</div>`;
            setTimeout(() => container.innerHTML = '', 5000);
        }}

        // Custom service name toggle
        document.getElementById('service_name').addEventListener('change', function() {{
            const customGroup = document.getElementById('custom-name-group');
            customGroup.style.display = this.value === 'custom' ? 'block' : 'none';
        }});

        // Load tokens
        async function loadTokens() {{
            try {{
                const response = await fetch(`${{API_BASE}}/admin/service-tokens`, {{
                    credentials: 'include'
                }});

                if (response.status === 401) {{
                    window.location.href = `${{API_BASE}}/admin/auth/login`;
                    return;
                }}

                const data = await response.json();
                const tokens = data.tokens || [];

                document.getElementById('tokens-loading').classList.add('hidden');

                if (tokens.length === 0) {{
                    document.getElementById('tokens-empty').classList.remove('hidden');
                    document.getElementById('tokens-table').classList.add('hidden');
                }} else {{
                    document.getElementById('tokens-empty').classList.add('hidden');
                    document.getElementById('tokens-table').classList.remove('hidden');

                    const tbody = document.getElementById('tokens-body');
                    tbody.innerHTML = tokens.map(t => `
                        <tr>
                            <td><strong>${{t.service_name}}</strong></td>
                            <td>${{t.description || '-'}}</td>
                            <td>${{formatDate(t.created_at)}}</td>
                            <td>${{t.last_used_at ? formatDate(t.last_used_at) : 'Never'}}</td>
                            <td class="${{t.enabled ? 'status-enabled' : 'status-disabled'}}">
                                ${{t.enabled ? 'Active' : 'Revoked'}}
                            </td>
                            <td>
                                ${{t.enabled ? `<button class="btn btn-danger btn-sm" onclick="revokeToken('${{t.service_name}}')">Revoke</button>` : ''}}
                            </td>
                        </tr>
                    `).join('');
                }}
            }} catch (error) {{
                document.getElementById('tokens-loading').innerHTML = 'Error loading tokens';
                console.error('Failed to load tokens:', error);
            }}
        }}

        // Create token
        async function createToken(event) {{
            event.preventDefault();

            let serviceName = document.getElementById('service_name').value;
            if (serviceName === 'custom') {{
                serviceName = document.getElementById('custom_name').value;
            }}

            const description = document.getElementById('description').value;

            if (!serviceName) {{
                showAlert('Please select or enter a service name', 'error');
                return;
            }}

            try {{
                const response = await fetch(`${{API_BASE}}/admin/service-tokens`, {{
                    method: 'POST',
                    credentials: 'include',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ service_name: serviceName, description }})
                }});

                const data = await response.json();

                if (response.ok) {{
                    document.getElementById('new-token-value').textContent = data.token;
                    document.getElementById('new-token-display').classList.remove('hidden');
                    showAlert(`Token created for ${{serviceName}}`, 'success');
                    loadTokens();
                }} else {{
                    showAlert(data.detail || 'Failed to create token', 'error');
                }}
            }} catch (error) {{
                showAlert('Error creating token', 'error');
                console.error('Failed to create token:', error);
            }}
        }}

        // Copy token
        function copyToken() {{
            const token = document.getElementById('new-token-value').textContent;
            navigator.clipboard.writeText(token).then(() => {{
                showAlert('Token copied to clipboard', 'success');
            }});
        }}

        // Revoke token
        async function revokeToken(serviceName) {{
            if (!confirm(`Are you sure you want to revoke the token for ${{serviceName}}?`)) {{
                return;
            }}

            try {{
                const response = await fetch(`${{API_BASE}}/admin/service-tokens/${{serviceName}}`, {{
                    method: 'DELETE',
                    credentials: 'include'
                }});

                if (response.ok) {{
                    showAlert(`Token revoked for ${{serviceName}}`, 'success');
                    loadTokens();
                }} else {{
                    const data = await response.json();
                    showAlert(data.detail || 'Failed to revoke token', 'error');
                }}
            }} catch (error) {{
                showAlert('Error revoking token', 'error');
                console.error('Failed to revoke token:', error);
            }}
        }}

        // Load logs
        async function filterLogs(event) {{
            if (event) event.preventDefault();

            const service = document.getElementById('log_service').value;
            const level = document.getElementById('log_level').value;
            const limit = document.getElementById('log_limit').value;

            document.getElementById('logs-loading').classList.remove('hidden');
            document.getElementById('logs-empty').classList.add('hidden');
            document.getElementById('logs-table').classList.add('hidden');

            try {{
                let url = `${{API_BASE}}/admin/service-logs?limit=${{limit}}`;
                if (service) url += `&service_name=${{service}}`;
                if (level) url += `&level=${{level}}`;

                const response = await fetch(url, {{ credentials: 'include' }});
                const data = await response.json();
                const logs = data.logs || [];

                document.getElementById('logs-loading').classList.add('hidden');

                if (logs.length === 0) {{
                    document.getElementById('logs-empty').classList.remove('hidden');
                    document.getElementById('logs-empty').textContent = 'No logs found matching criteria.';
                }} else {{
                    document.getElementById('logs-table').classList.remove('hidden');

                    const tbody = document.getElementById('logs-body');
                    tbody.innerHTML = logs.map(log => `
                        <tr>
                            <td style="white-space: nowrap;">${{formatDate(log.timestamp)}}</td>
                            <td>${{log.service_name}}</td>
                            <td class="${{log.level === 'ERROR' ? 'status-disabled' : log.level === 'WARNING' ? 'status-enabled' : ''}}">
                                ${{log.level}}
                            </td>
                            <td>${{log.event || '-'}}</td>
                            <td style="max-width: 400px; overflow: hidden; text-overflow: ellipsis;">
                                ${{log.message || '-'}}
                            </td>
                        </tr>
                    `).join('');
                }}
            }} catch (error) {{
                document.getElementById('logs-loading').classList.add('hidden');
                document.getElementById('logs-empty').classList.remove('hidden');
                document.getElementById('logs-empty').textContent = 'Error loading logs.';
                console.error('Failed to load logs:', error);
            }}
        }}

        // Format date
        function formatDate(isoString) {{
            if (!isoString) return '-';
            const date = new Date(isoString);
            return date.toLocaleString();
        }}

        // Logout
        async function logout() {{
            await fetch(`${{API_BASE}}/admin/auth/logout`, {{
                method: 'POST',
                credentials: 'include'
            }});
            window.location.href = '/lens/';
        }}

        // Initial load
        loadTokens();
    </script>
</body>
</html>
    """)


# OAuth routes
@app.get("/api/admin/auth/login")
async def oauth_login():
    """Initiate OAuth flow"""
    # For development, mock the OAuth flow
    if OAUTH_CLIENT_ID == "mock-client-id":
        # Development mode - auto-authenticate
        mock_user = OAuthUser(email="dev@ciris.ai", name="Development User", hd="ciris.ai")
        session_id = create_session(mock_user)
        response = RedirectResponse(url="/lens/admin/", status_code=302)
        response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")
        return response

    # Production OAuth flow
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_CALLBACK_URL,
        "response_type": "code",
        "scope": "openid email profile",
        "hd": ALLOWED_DOMAIN,
        "prompt": "select_account",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@app.get("/api/admin/auth/callback")
async def oauth_callback(code: str, state: str | None = None):
    """Handle OAuth callback from Google"""
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": OAUTH_CALLBACK_URL,
            },
        )

        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            return HTMLResponse(
                f"<h1>Authentication failed</h1><p>{token_response.text}</p>", status_code=400
            )

        tokens = token_response.json()
        access_token = tokens.get("access_token")

        # Get user info
        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if userinfo_response.status_code != 200:
            logger.error(f"User info fetch failed: {userinfo_response.text}")
            return HTMLResponse("<h1>Failed to get user info</h1>", status_code=400)

        userinfo = userinfo_response.json()

    # Verify domain
    hd = userinfo.get("hd", "")
    if hd != ALLOWED_DOMAIN:
        return HTMLResponse(
            f"<h1>Access denied</h1><p>Only @{ALLOWED_DOMAIN} accounts allowed</p>", status_code=403
        )

    # Create session
    user = OAuthUser(email=userinfo.get("email"), name=userinfo.get("name"), hd=hd)

    session_id = create_session(user)
    response = RedirectResponse(url="/lens/admin/", status_code=302)
    response.set_cookie(
        key="session_id", value=session_id, httponly=True,
        samesite="lax", secure=IS_PRODUCTION
    )
    return response


@app.get("/api/admin/auth/status")
async def auth_status(request: Request):
    """Check authentication status"""
    user = get_current_user(request)
    return {"authenticated": user is not None, "user": user}


@app.post("/api/admin/auth/logout")
async def logout(request: Request):
    """Logout and clear session"""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in sessions:
        del sessions[session_id]

    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("session_id")
    return response


# Configuration management routes
@app.get("/api/admin/configurations")
async def get_configurations(user: dict = Depends(require_auth)):
    """Get all telemetry and visibility configurations"""
    return {"telemetry": telemetry_configs, "visibility": visibility_configs}


@app.get("/api/admin/agents")
async def get_agents(user: dict = Depends(require_auth)):
    """Get all discovered agents from manager"""
    try:
        # Query the manager API (no auth needed)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{MANAGER_API_URL}/agents")
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch agents: {e}")

    # Return mock data for development
    return {
        "agents": [
            {
                "agent_id": "datum",
                "name": "Datum",
                "status": "running",
                "cognitive_state": "WORK",
                "version": "1.4.4-beta",
                "codename": "Graceful Guardian",
                "api_port": 8080,
                "health": "healthy",
            }
        ]
    }


# Telemetry configuration routes
@app.get("/api/admin/telemetry/{agent_id}")
async def get_telemetry_config(agent_id: str, user: dict = Depends(require_auth)):
    """Get telemetry configuration for an agent"""
    return telemetry_configs.get(agent_id, {"agent_id": agent_id, "enabled": False})


@app.put("/api/admin/telemetry/{agent_id}")
async def update_telemetry_config(
    agent_id: str, config: TelemetryConfig, user: dict = Depends(require_auth)
):
    """Update telemetry configuration for an agent"""
    config.last_updated = datetime.now(UTC).isoformat()
    config.updated_by = user["email"]
    telemetry_configs[agent_id] = config.dict()
    return {"status": "updated", "config": config}


@app.patch("/api/admin/telemetry/{agent_id}")
async def patch_telemetry_config(
    agent_id: str, updates: dict[str, Any], user: dict = Depends(require_auth)
):
    """Partially update telemetry configuration"""
    config = telemetry_configs.get(agent_id, {"agent_id": agent_id})
    config.update(updates)
    config["last_updated"] = datetime.now(UTC).isoformat()
    config["updated_by"] = user["email"]
    telemetry_configs[agent_id] = config
    return {"status": "updated", "config": config}


# Visibility configuration routes
@app.get("/api/admin/visibility/{agent_id}")
async def get_visibility_config(agent_id: str, user: dict = Depends(require_auth)):
    """Get visibility configuration for an agent"""
    return visibility_configs.get(
        agent_id, {"agent_id": agent_id, "public_visible": False, "redact_pii": True}
    )


@app.put("/api/admin/visibility/{agent_id}")
async def update_visibility_config(
    agent_id: str, config: VisibilityConfig, user: dict = Depends(require_auth)
):
    """Update visibility configuration for an agent"""
    config.last_updated = datetime.now(UTC).isoformat()
    config.updated_by = user["email"]
    config.redact_pii = True  # Always enforce PII redaction
    visibility_configs[agent_id] = config.dict()
    return {"status": "updated", "config": config}


@app.patch("/api/admin/visibility/{agent_id}")
async def patch_visibility_config(
    agent_id: str, updates: dict[str, Any], user: dict = Depends(require_auth)
):
    """Partially update visibility configuration"""
    config = visibility_configs.get(agent_id, {"agent_id": agent_id})
    config.update(updates)
    config["last_updated"] = datetime.now(UTC).isoformat()
    config["updated_by"] = user["email"]
    config["redact_pii"] = True  # Always enforce PII redaction
    visibility_configs[agent_id] = config
    return {"status": "updated", "config": config}


# Manager management routes
@app.get("/api/admin/managers")
async def get_managers(user: dict = Depends(require_auth)):
    """Get all registered managers"""
    if not db_pool:
        return {"managers": []}

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, url, description, enabled, last_seen, last_error,
                   collection_interval_seconds, added_at
            FROM managers
            ORDER BY name
        """)

        managers = []
        for row in rows:
            managers.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "url": row["url"],
                    "description": row["description"],
                    "enabled": row["enabled"],
                    "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                    "last_error": row["last_error"],
                    "collection_interval_seconds": row["collection_interval_seconds"],
                    "added_at": row["added_at"].isoformat() if row["added_at"] else None,
                }
            )

        return {"managers": managers}


@app.post("/api/admin/managers")
async def add_manager(config: ManagerConfig, user: dict = Depends(require_auth)):
    """Add a new manager to monitor"""
    if not db_pool:
        raise HTTPException(status_code=503, detail=ERR_DATABASE_NOT_AVAILABLE)

    try:
        # Add to database and start collection
        manager_id = await manager_collector.add_manager(
            name=config.name,
            url=config.url,
            description=config.description,
            auth_token=config.auth_token,
            collection_interval=config.collection_interval_seconds,
        )

        return {
            "status": "created",
            "manager_id": manager_id,
            "message": f"Manager '{config.name}' added successfully",
        }
    except Exception as e:
        logger.error(f"Failed to add manager: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.put("/api/admin/managers/{manager_id}")
async def update_manager(
    manager_id: int, updates: ManagerUpdate, user: dict = Depends(require_auth)
):
    """Update a manager configuration"""
    if not db_pool:
        raise HTTPException(status_code=503, detail=ERR_DATABASE_NOT_AVAILABLE)

    async with db_pool.acquire() as conn:
        # Build update query dynamically based on provided fields
        update_fields = []
        values = []
        param_count = 1

        if updates.name is not None:
            update_fields.append(f"name = ${param_count}")
            values.append(updates.name)
            param_count += 1

        if updates.url is not None:
            update_fields.append(f"url = ${param_count}")
            values.append(updates.url)
            param_count += 1

        if updates.description is not None:
            update_fields.append(f"description = ${param_count}")
            values.append(updates.description)
            param_count += 1

        if updates.auth_token is not None:
            update_fields.append(f"auth_token = ${param_count}")
            values.append(updates.auth_token)
            param_count += 1

        if updates.collection_interval_seconds is not None:
            update_fields.append(f"collection_interval_seconds = ${param_count}")
            values.append(updates.collection_interval_seconds)
            param_count += 1

        if updates.enabled is not None:
            update_fields.append(f"enabled = ${param_count}")
            values.append(updates.enabled)
            param_count += 1

        if not update_fields:
            return {"status": "no_changes"}

        values.append(manager_id)
        # Fields are validated against known names, values are parameterized
        query = f"UPDATE managers SET {', '.join(update_fields)} WHERE id = ${param_count}"  # noqa: S608

        await conn.execute(query, *values)

    return {"status": "updated", "manager_id": manager_id}


@app.delete("/api/admin/managers/{manager_id}")
async def delete_manager(manager_id: int, user: dict = Depends(require_auth)):
    """Remove a manager (disable collection)"""
    if not manager_collector:
        raise HTTPException(status_code=503, detail="Manager collector not available")

    await manager_collector.remove_manager(manager_id)
    return {"status": "deleted", "manager_id": manager_id}


@app.get("/api/admin/managers/{manager_id}/agents")
async def get_manager_agents(manager_id: int, user: dict = Depends(require_auth)):
    """Get agents discovered by a specific manager"""
    if not db_pool:
        return {"agents": []}

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT agent_id, agent_name, status, cognitive_state, version,
                   codename, api_port, health, template, deployment, last_seen
            FROM discovered_agents
            WHERE manager_id = $1 AND last_seen > NOW() - INTERVAL '5 minutes'
            ORDER BY agent_name
        """,
            manager_id,
        )

        agents = []
        for row in rows:
            agents.append(
                {
                    "agent_id": row["agent_id"],
                    "agent_name": row["agent_name"],
                    "status": row["status"],
                    "cognitive_state": row["cognitive_state"],
                    "version": row["version"],
                    "codename": row["codename"],
                    "api_port": row["api_port"],
                    "health": row["health"],
                    "template": row["template"],
                    "deployment": row["deployment"],
                    "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                }
            )

        return {"agents": agents}


@app.get("/api/admin/agents/all")
async def get_all_discovered_agents(user: dict = Depends(require_auth)):
    """Get all discovered agents from all managers"""
    if not db_pool:
        return {"agents": []}

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT da.*, m.name as manager_name, m.url as manager_url
            FROM discovered_agents da
            JOIN managers m ON da.manager_id = m.id
            WHERE m.enabled = true AND da.last_seen > NOW() - INTERVAL '5 minutes'
            ORDER BY da.agent_name
        """)

        agents = []
        for row in rows:
            agents.append(
                {
                    "agent_id": row["agent_id"],
                    "agent_name": row["agent_name"],
                    "status": row["status"],
                    "cognitive_state": row["cognitive_state"],
                    "version": row["version"],
                    "codename": row["codename"],
                    "api_port": row["api_port"],
                    "health": row["health"],
                    "template": row["template"],
                    "deployment": row["deployment"],
                    "manager_name": row["manager_name"],
                    "manager_url": row["manager_url"],
                    "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                }
            )

        return {"agents": agents}


@app.get("/api/admin/stats")
async def get_stats(user: dict = Depends(require_auth)):
    """Get overall statistics"""
    if not manager_collector:
        return {"stats": {}}

    stats = await manager_collector.get_manager_stats()
    return {"stats": stats}


# Agent token management endpoints
@app.get("/api/admin/agent-tokens")
async def get_agent_tokens(user: dict = Depends(require_auth)):
    """Get configured agent tokens (without actual token values)"""
    agents = await token_manager.get_configured_agents()
    return {"agents": agents}


@app.post("/api/admin/agent-tokens")
async def set_agent_token(config: AgentTokenConfig, user: dict = Depends(require_auth)):
    """Set or update an agent token"""
    success = await token_manager.set_agent_token(
        agent_name=config.agent_name, token=config.token, url=config.url, updated_by=user["email"]
    )

    if success:
        # Restart OTLP collector to pick up new token
        global otlp_collector
        if otlp_collector:
            await otlp_collector.stop()
            otlp_collector = OTLPCollector(DATABASE_URL)
            _ = asyncio.create_task(otlp_collector.start())

        return {"status": "success", "message": f"Token for {config.agent_name} has been updated"}
    else:
        raise HTTPException(status_code=500, detail="Failed to update token")


@app.delete("/api/admin/agent-tokens/{agent_name}")
async def remove_agent_token(agent_name: str, user: dict = Depends(require_auth)):
    """Remove an agent token"""
    success = await token_manager.remove_agent_token(agent_name)

    if success:
        # Restart OTLP collector
        global otlp_collector
        if otlp_collector:
            await otlp_collector.stop()
            otlp_collector = OTLPCollector(DATABASE_URL)
            _ = asyncio.create_task(otlp_collector.start())

        return {"status": "success", "message": f"Token for {agent_name} has been removed"}
    else:
        raise HTTPException(status_code=404, detail="Agent not found")


# ============================================
# Service Log Ingestion Endpoints
# ============================================


class ServiceTokenCreate(BaseModel):
    service_name: str
    description: str | None = None


@app.post("/api/v1/logs/ingest")
async def ingest_logs(request: Request):
    """
    Ingest logs from external services (Billing, Proxy, Manager).

    Authentication: Bearer token in Authorization header
    Content-Type: application/x-ndjson (newline-delimited JSON) or application/json

    Example:
        curl -X POST https://agents.ciris.ai/lens/api/v1/logs/ingest \
          -H "Authorization: Bearer svc_xxx" \
          -H "Content-Type: application/x-ndjson" \
          -d '{"timestamp":"2025-12-11T12:00:00Z","level":"INFO","event":"request_completed","message":"OK"}'
    """
    if not log_ingest_service:
        raise HTTPException(status_code=503, detail=ERR_LOG_INGEST_NOT_AVAILABLE)

    # Extract bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # Remove "Bearer "

    # Verify token
    service_name = await log_ingest_service.verify_token(token)
    if not service_name:
        raise HTTPException(status_code=401, detail="Invalid service token")

    # Parse body
    content_type = request.headers.get("Content-Type", "application/json")
    body = await request.body()

    try:
        if "ndjson" in content_type:
            # Newline-delimited JSON
            logs = []
            for line in body.decode("utf-8").strip().split("\n"):
                if line.strip():
                    logs.append(json.loads(line))
        else:
            # Regular JSON (single log or array)
            data = json.loads(body)
            logs = data if isinstance(data, list) else [data]
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    if not logs:
        return {"status": "ok", "accepted": 0, "rejected": 0, "errors": []}

    # Ingest logs
    result = await log_ingest_service.ingest_logs(service_name, logs)

    return {"status": "ok", **result}


@app.get("/api/admin/service-tokens")
async def get_service_tokens(user: dict = Depends(require_auth)):
    """Get all service tokens (without actual token values)."""
    if not log_ingest_service:
        raise HTTPException(status_code=503, detail=ERR_LOG_INGEST_NOT_AVAILABLE)

    tokens = await log_ingest_service.get_tokens()
    return {"tokens": tokens}


@app.post("/api/admin/service-tokens")
async def create_service_token(config: ServiceTokenCreate, user: dict = Depends(require_auth)):
    """
    Create a new service token.
    Returns the raw token - this is the only time it will be shown!
    """
    if not log_ingest_service:
        raise HTTPException(status_code=503, detail=ERR_LOG_INGEST_NOT_AVAILABLE)

    raw_token = await log_ingest_service.create_token(
        service_name=config.service_name, description=config.description, created_by=user["email"]
    )

    return {
        "status": "created",
        "service_name": config.service_name,
        "token": raw_token,
        "warning": "Save this token now - it cannot be retrieved later!",
    }


@app.delete("/api/admin/service-tokens/{service_name}")
async def revoke_service_token(service_name: str, user: dict = Depends(require_auth)):
    """Revoke a service token."""
    if not log_ingest_service:
        raise HTTPException(status_code=503, detail=ERR_LOG_INGEST_NOT_AVAILABLE)

    success = await log_ingest_service.revoke_token(service_name)

    if success:
        return {"status": "revoked", "service_name": service_name}
    else:
        raise HTTPException(status_code=404, detail="Service token not found")


@app.get("/api/admin/service-logs")
async def get_service_logs(
    service_name: str | None = None,
    level: str | None = None,
    limit: int = 100,
    user: dict = Depends(require_auth),
):
    """Get recent service logs with optional filtering."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=ERR_DATABASE_NOT_AVAILABLE)

    async with db_pool.acquire() as conn:
        query = """
            SELECT service_name, server_id, timestamp, level, event, logger,
                   message, request_id, trace_id, user_hash, attributes
            FROM cirislens.service_logs
            WHERE 1=1
        """
        params = []
        param_count = 1

        if service_name:
            query += f" AND service_name = ${param_count}"
            params.append(service_name)
            param_count += 1

        if level:
            query += f" AND level = ${param_count}"
            params.append(level.upper())
            param_count += 1

        query += f" ORDER BY timestamp DESC LIMIT ${param_count}"
        params.append(min(limit, 1000))

        rows = await conn.fetch(query, *params)

        logs = [
            {
                "service_name": row["service_name"],
                "server_id": row["server_id"],
                "timestamp": row["timestamp"].isoformat(),
                "level": row["level"],
                "event": row["event"],
                "logger": row["logger"],
                "message": row["message"],
                "request_id": row["request_id"],
                "trace_id": row["trace_id"],
                "user_hash": row["user_hash"],
                "attributes": row["attributes"],
            }
            for row in rows
        ]

        return {"logs": logs, "count": len(logs)}


# WebSocket endpoint placeholder
@app.websocket("/ws/admin/agents")
async def websocket_agents(websocket):
    """WebSocket for real-time agent updates"""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            # In production, stream agent updates
            await websocket.send_json({"type": "heartbeat"})
    except Exception:
        pass
    finally:
        await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
