"""
CIRISLens API Service
Mock implementation for development
"""

from fastapi import FastAPI, HTTPException, Depends, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import httpx
import asyncpg
import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
import logging
import asyncio
from manager_collector import ManagerCollector
from otlp_collector import OTLPCollector

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="CIRISLens API",
    description="Telemetry and Observability Platform for CIRIS",
    version="0.1.0-dev"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:3000", "https://agents.ciris.ai"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from environment
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "mock-client-id")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "mock-secret")
OAUTH_CALLBACK_URL = os.getenv("OAUTH_CALLBACK_URL", "http://localhost:8080/cirislens/api/admin/auth/callback")
MANAGER_API_URL = os.getenv("MANAGER_API_URL", "http://host.docker.internal:8888/manager/v1")
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "ciris.ai")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")

# In-memory storage for development
sessions = {}
telemetry_configs = {}
visibility_configs = {}

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname")
db_pool = None
manager_collector = None
otlp_collector = None

# Models
class OAuthUser(BaseModel):
    email: EmailStr
    name: str
    picture: Optional[str] = None
    hd: Optional[str] = None

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
    description: Optional[str] = ""
    auth_token: Optional[str] = None
    collection_interval_seconds: int = 30
    enabled: bool = True

class ManagerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    auth_token: Optional[str] = None
    collection_interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None

# Session management
def create_session(user: OAuthUser) -> str:
    """Create a new session for authenticated user"""
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "user": user.dict(),
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat()
    }
    return session_id

def get_current_user(request: Request) -> Optional[Dict]:
    """Get current user from session"""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        return None
    
    session = sessions[session_id]
    if datetime.fromisoformat(session["expires_at"]) < datetime.utcnow():
        del sessions[session_id]
        return None
    
    return session["user"]

def require_auth(request: Request) -> Dict:
    """Dependency to require authentication"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

# Startup and shutdown events
@app.on_event("startup")
async def startup():
    """Initialize database and start collectors"""
    global db_pool, manager_collector, otlp_collector
    
    try:
        # Create database pool
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        logger.info("Database pool created")
        
        # Initialize tables
        async with db_pool.acquire() as conn:
            # Create managers tables
            with open("/app/sql/manager_tables.sql", "r") as f:
                await conn.execute(f.read())
            # Create OTLP tables
            with open("/app/sql/otlp_tables.sql", "r") as f:
                await conn.execute(f.read())
            logger.info("Database tables initialized")
            
        # Start manager collector
        manager_collector = ManagerCollector(DATABASE_URL)
        asyncio.create_task(manager_collector.start())
        logger.info("Manager collector started")
        
        # Start OTLP collector if enabled
        if os.getenv("OTLP_COLLECTION_ENABLED", "true").lower() == "true":
            otlp_collector = OTLPCollector(DATABASE_URL)
            asyncio.create_task(otlp_collector.start())
            logger.info("OTLP collector started")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
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
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Admin interface routes
@app.get("/admin/")
async def admin_interface(request: Request):
    """Serve admin interface"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/api/admin/auth/login")
    
    # In production, this would serve the actual admin HTML
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head><title>CIRISLens Admin</title></head>
    <body>
        <h1>CIRISLens Admin Interface</h1>
        <p>Logged in as: """ + user['email'] + """</p>
        <p>This is a mock interface for development.</p>
        <a href="/api/admin/auth/logout">Logout</a>
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
        mock_user = OAuthUser(
            email="dev@ciris.ai",
            name="Development User",
            hd="ciris.ai"
        )
        session_id = create_session(mock_user)
        response = RedirectResponse(url="/cirislens/admin/", status_code=302)
        response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")
        return response
    
    # Production OAuth flow
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_CALLBACK_URL,
        "response_type": "code",
        "scope": "openid email profile",
        "hd": ALLOWED_DOMAIN,
        "prompt": "select_account"
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")

@app.post("/api/admin/auth/callback")
async def oauth_callback(code: str, state: Optional[str] = None):
    """Handle OAuth callback"""
    # In production, exchange code for token and get user info
    # For now, mock the response
    mock_user = OAuthUser(
        email="dev@ciris.ai",
        name="Development User",
        hd="ciris.ai"
    )
    
    session_id = create_session(mock_user)
    return {
        "authenticated": True,
        "session_id": session_id,
        "user": mock_user.dict()
    }

@app.get("/api/admin/auth/status")
async def auth_status(request: Request):
    """Check authentication status"""
    user = get_current_user(request)
    return {
        "authenticated": user is not None,
        "user": user
    }

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
async def get_configurations(user: Dict = Depends(require_auth)):
    """Get all telemetry and visibility configurations"""
    return {
        "telemetry": telemetry_configs,
        "visibility": visibility_configs
    }

@app.get("/api/admin/agents")
async def get_agents(user: Dict = Depends(require_auth)):
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
                "health": "healthy"
            }
        ]
    }

# Telemetry configuration routes
@app.get("/api/admin/telemetry/{agent_id}")
async def get_telemetry_config(agent_id: str, user: Dict = Depends(require_auth)):
    """Get telemetry configuration for an agent"""
    return telemetry_configs.get(agent_id, {
        "agent_id": agent_id,
        "enabled": False
    })

@app.put("/api/admin/telemetry/{agent_id}")
async def update_telemetry_config(
    agent_id: str, 
    config: TelemetryConfig,
    user: Dict = Depends(require_auth)
):
    """Update telemetry configuration for an agent"""
    config.last_updated = datetime.utcnow().isoformat()
    config.updated_by = user["email"]
    telemetry_configs[agent_id] = config.dict()
    return {"status": "updated", "config": config}

@app.patch("/api/admin/telemetry/{agent_id}")
async def patch_telemetry_config(
    agent_id: str,
    updates: Dict[str, Any],
    user: Dict = Depends(require_auth)
):
    """Partially update telemetry configuration"""
    config = telemetry_configs.get(agent_id, {"agent_id": agent_id})
    config.update(updates)
    config["last_updated"] = datetime.utcnow().isoformat()
    config["updated_by"] = user["email"]
    telemetry_configs[agent_id] = config
    return {"status": "updated", "config": config}

# Visibility configuration routes
@app.get("/api/admin/visibility/{agent_id}")
async def get_visibility_config(agent_id: str, user: Dict = Depends(require_auth)):
    """Get visibility configuration for an agent"""
    return visibility_configs.get(agent_id, {
        "agent_id": agent_id,
        "public_visible": False,
        "redact_pii": True
    })

@app.put("/api/admin/visibility/{agent_id}")
async def update_visibility_config(
    agent_id: str,
    config: VisibilityConfig,
    user: Dict = Depends(require_auth)
):
    """Update visibility configuration for an agent"""
    config.last_updated = datetime.utcnow().isoformat()
    config.updated_by = user["email"]
    config.redact_pii = True  # Always enforce PII redaction
    visibility_configs[agent_id] = config.dict()
    return {"status": "updated", "config": config}

@app.patch("/api/admin/visibility/{agent_id}")
async def patch_visibility_config(
    agent_id: str,
    updates: Dict[str, Any],
    user: Dict = Depends(require_auth)
):
    """Partially update visibility configuration"""
    config = visibility_configs.get(agent_id, {"agent_id": agent_id})
    config.update(updates)
    config["last_updated"] = datetime.utcnow().isoformat()
    config["updated_by"] = user["email"]
    config["redact_pii"] = True  # Always enforce PII redaction
    visibility_configs[agent_id] = config
    return {"status": "updated", "config": config}

# Manager management routes
@app.get("/api/admin/managers")
async def get_managers(user: Dict = Depends(require_auth)):
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
            managers.append({
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "description": row["description"],
                "enabled": row["enabled"],
                "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                "last_error": row["last_error"],
                "collection_interval_seconds": row["collection_interval_seconds"],
                "added_at": row["added_at"].isoformat() if row["added_at"] else None
            })
            
        return {"managers": managers}

@app.post("/api/admin/managers")
async def add_manager(config: ManagerConfig, user: Dict = Depends(require_auth)):
    """Add a new manager to monitor"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
        
    try:
        # Add to database and start collection
        manager_id = await manager_collector.add_manager(
            name=config.name,
            url=config.url,
            description=config.description,
            auth_token=config.auth_token,
            collection_interval=config.collection_interval_seconds
        )
        
        return {
            "status": "created",
            "manager_id": manager_id,
            "message": f"Manager '{config.name}' added successfully"
        }
    except Exception as e:
        logger.error(f"Failed to add manager: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/admin/managers/{manager_id}")
async def update_manager(manager_id: int, updates: ManagerUpdate, user: Dict = Depends(require_auth)):
    """Update a manager configuration"""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
        
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
        query = f"UPDATE managers SET {', '.join(update_fields)} WHERE id = ${param_count}"
        
        await conn.execute(query, *values)
        
    return {"status": "updated", "manager_id": manager_id}

@app.delete("/api/admin/managers/{manager_id}")
async def delete_manager(manager_id: int, user: Dict = Depends(require_auth)):
    """Remove a manager (disable collection)"""
    if not manager_collector:
        raise HTTPException(status_code=503, detail="Manager collector not available")
        
    await manager_collector.remove_manager(manager_id)
    return {"status": "deleted", "manager_id": manager_id}

@app.get("/api/admin/managers/{manager_id}/agents")
async def get_manager_agents(manager_id: int, user: Dict = Depends(require_auth)):
    """Get agents discovered by a specific manager"""
    if not db_pool:
        return {"agents": []}
        
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT agent_id, agent_name, status, cognitive_state, version, 
                   codename, api_port, health, template, deployment, last_seen
            FROM discovered_agents
            WHERE manager_id = $1 AND last_seen > NOW() - INTERVAL '5 minutes'
            ORDER BY agent_name
        """, manager_id)
        
        agents = []
        for row in rows:
            agents.append({
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
                "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None
            })
            
        return {"agents": agents}

@app.get("/api/admin/agents/all")
async def get_all_discovered_agents(user: Dict = Depends(require_auth)):
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
            agents.append({
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
                "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None
            })
            
        return {"agents": agents}

@app.get("/api/admin/stats")
async def get_stats(user: Dict = Depends(require_auth)):
    """Get overall statistics"""
    if not manager_collector:
        return {"stats": {}}
        
    stats = await manager_collector.get_manager_stats()
    return {"stats": stats}

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