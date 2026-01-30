"""
Schema Management API for CIRISLens.

Provides endpoints for:
- Listing registered trace schemas
- Getting schema definitions
- Syncing schemas from remote repository
- Manually registering schemas (admin)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import get_db_pool
from api.schema_sync import (
    SyncResult,
    fetch_schemas_from_remote,
    get_schema_cache,
    initialize_schema_cache,
    load_schemas_from_directory,
    refresh_schema_cache,
    sync_schemas_to_database,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/schemas", tags=["schemas"])

# Default schema repository URL (can be overridden via environment)
SCHEMA_REPOSITORY_URL = os.getenv(
    "CIRISLENS_SCHEMA_REPOSITORY_URL",
    "https://schemas.ciris.ai/traces/",
)

# Local schema directory (relative to project root)
LOCAL_SCHEMA_DIR = Path(__file__).parent.parent / "schemas"


class SchemaListItem(BaseModel):
    """Schema list item response."""

    version: str
    description: str | None
    status: str
    event_count: int


class SchemaDetail(BaseModel):
    """Full schema detail response."""

    version: str
    description: str | None
    status: str
    signature_event_types: list[str]
    required_event_types: list[str] | None
    optional_event_types: list[str] | None
    field_count: int
    source_url: str | None
    synced_at: str | None


class SyncResponse(BaseModel):
    """Schema sync response."""

    synced_count: int
    updated_count: int
    schemas: list[str]
    errors: list[str]


class SchemaRegisterRequest(BaseModel):
    """Request to register a schema manually."""

    version: str
    description: str
    status: str = "current"
    signature_event_types: list[str]
    required_event_types: list[str] | None = None
    optional_event_types: list[str] | None = None
    field_extractions: dict[str, dict[str, Any]] | None = None


@router.get("", response_model=list[SchemaListItem])
async def list_schemas(db_pool=Depends(get_db_pool)) -> list[SchemaListItem]:
    """List all registered trace schemas.

    Returns basic info about each schema (version, status, event count).
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ts.version,
                ts.description,
                ts.status,
                COALESCE(array_length(ts.signature_event_types, 1), 0) as event_count
            FROM cirislens.trace_schemas ts
            ORDER BY CASE ts.status
                WHEN 'current' THEN 0
                WHEN 'supported' THEN 1
                ELSE 2
            END, ts.version
            """
        )

    return [
        SchemaListItem(
            version=row["version"],
            description=row["description"],
            status=row["status"],
            event_count=row["event_count"],
        )
        for row in rows
    ]


@router.get("/cache/status")
async def get_cache_status() -> dict[str, Any]:
    """Get current schema cache status.

    Returns info about loaded schemas and cache state.
    """
    cache = get_schema_cache()
    return {
        "loaded": cache.is_loaded,
        "schema_count": len(cache.schema_versions()),
        "schemas": cache.schema_versions(),
        "schemas_by_status": {
            schema.version: schema.status
            for schema in cache.schemas_by_priority()
        },
    }


@router.post("/cache/refresh")
async def refresh_cache(db_pool=Depends(get_db_pool)) -> dict[str, Any]:
    """Refresh the schema cache from database.

    Call this after modifying schemas to update the in-memory cache.
    """
    async with db_pool.acquire() as conn:
        await refresh_schema_cache(conn)

    cache = get_schema_cache()
    return {
        "status": "refreshed",
        "schema_count": len(cache.schema_versions()),
        "schemas": cache.schema_versions(),
    }


@router.get("/{version}", response_model=SchemaDetail)
async def get_schema(version: str, db_pool=Depends(get_db_pool)) -> SchemaDetail:
    """Get full details for a specific schema version.

    Includes field extraction rules and metadata.
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                ts.version,
                ts.description,
                ts.status,
                ts.signature_event_types,
                ts.required_event_types,
                ts.optional_event_types,
                ts.source_url,
                ts.synced_at,
                COUNT(tsf.id) as field_count
            FROM cirislens.trace_schemas ts
            LEFT JOIN cirislens.trace_schema_fields tsf ON tsf.schema_version = ts.version
            WHERE ts.version = $1
            GROUP BY ts.version, ts.description, ts.status, ts.signature_event_types,
                     ts.required_event_types, ts.optional_event_types, ts.source_url, ts.synced_at
            """,
            version,
        )

    if not row:
        raise HTTPException(status_code=404, detail=f"Schema {version} not found")

    return SchemaDetail(
        version=row["version"],
        description=row["description"],
        status=row["status"],
        signature_event_types=row["signature_event_types"] or [],
        required_event_types=row["required_event_types"],
        optional_event_types=row["optional_event_types"],
        field_count=row["field_count"],
        source_url=row["source_url"],
        synced_at=row["synced_at"].isoformat() if row["synced_at"] else None,
    )


@router.get("/{version}/fields")
async def get_schema_fields(version: str, db_pool=Depends(get_db_pool)) -> dict[str, list[dict[str, Any]]]:
    """Get field extraction rules for a schema.

    Returns fields grouped by event_type.
    """
    async with db_pool.acquire() as conn:
        # Check schema exists
        exists = await conn.fetchval(
            "SELECT 1 FROM cirislens.trace_schemas WHERE version = $1",
            version,
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"Schema {version} not found")

        rows = await conn.fetch(
            """
            SELECT event_type, field_name, json_path, data_type, required, db_column, description
            FROM cirislens.trace_schema_fields
            WHERE schema_version = $1
            ORDER BY event_type, field_name
            """,
            version,
        )

    # Group by event_type
    fields: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_type = row["event_type"]
        if event_type not in fields:
            fields[event_type] = []
        fields[event_type].append({
            "field_name": row["field_name"],
            "json_path": row["json_path"],
            "data_type": row["data_type"],
            "required": row["required"],
            "db_column": row["db_column"],
            "description": row["description"],
        })

    return fields


@router.post("/sync", response_model=SyncResponse)
async def sync_schemas(
    source: str = "local",  # "local" or "remote"
    db_pool=Depends(get_db_pool),
) -> SyncResponse:
    """Sync schemas from source to database.

    Args:
        source: "local" to sync from local schemas/ directory,
                "remote" to fetch from SCHEMA_REPOSITORY_URL

    After syncing, refreshes the in-memory cache.
    """
    # Load schemas from source
    if source == "local":
        if not LOCAL_SCHEMA_DIR.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Local schema directory not found: {LOCAL_SCHEMA_DIR}",
            )
        schemas = load_schemas_from_directory(LOCAL_SCHEMA_DIR)
    elif source == "remote":
        schemas = await fetch_schemas_from_remote(SCHEMA_REPOSITORY_URL)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source: {source}. Use 'local' or 'remote'.",
        )

    if not schemas:
        raise HTTPException(
            status_code=400,
            detail=f"No schemas loaded from {source}",
        )

    # Sync to database
    async with db_pool.acquire() as conn:
        result = await sync_schemas_to_database(schemas, conn)

        # Refresh cache
        await refresh_schema_cache(conn)

    return SyncResponse(
        synced_count=result.synced_count,
        updated_count=result.updated_count,
        schemas=result.schemas,
        errors=result.errors,
    )


@router.post("", response_model=SchemaDetail)
async def register_schema(
    request: SchemaRegisterRequest,
    db_pool=Depends(get_db_pool),
) -> SchemaDetail:
    """Manually register a new schema.

    For admin use to add custom schemas.
    """
    async with db_pool.acquire() as conn:
        # Check if version already exists
        exists = await conn.fetchval(
            "SELECT 1 FROM cirislens.trace_schemas WHERE version = $1",
            request.version,
        )
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f"Schema {request.version} already exists. Use PUT to update.",
            )

        # Insert schema
        await conn.execute(
            """
            INSERT INTO cirislens.trace_schemas
                (version, description, status, definition, signature_event_types,
                 required_event_types, optional_event_types)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            request.version,
            request.description,
            request.status,
            json.dumps({"version": request.version, "description": request.description}),
            request.signature_event_types,
            request.required_event_types,
            request.optional_event_types,
        )

        # Insert field extraction rules if provided
        if request.field_extractions:
            for event_type, fields in request.field_extractions.items():
                for field_name, rule in fields.items():
                    await conn.execute(
                        """
                        INSERT INTO cirislens.trace_schema_fields
                            (schema_version, event_type, field_name, json_path,
                             data_type, required, db_column, description)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        request.version,
                        event_type,
                        field_name,
                        rule.get("path", ""),
                        rule.get("type", "string"),
                        rule.get("required", False),
                        rule.get("db_column", field_name),
                        rule.get("description", ""),
                    )

        # Refresh cache
        await refresh_schema_cache(conn)

    logger.info("SCHEMA_REGISTERED version=%s status=%s", request.version, request.status)

    return SchemaDetail(
        version=request.version,
        description=request.description,
        status=request.status,
        signature_event_types=request.signature_event_types,
        required_event_types=request.required_event_types,
        optional_event_types=request.optional_event_types,
        field_count=sum(len(f) for f in (request.field_extractions or {}).values()),
        source_url=None,
        synced_at=None,
    )


@router.delete("/{version}")
async def delete_schema(version: str, db_pool=Depends(get_db_pool)) -> dict[str, str]:
    """Delete a schema.

    Warning: This will delete the schema and all its field extraction rules.
    Active traces using this schema will fail validation until schema is restored.
    """
    async with db_pool.acquire() as conn:
        # Check schema exists
        exists = await conn.fetchval(
            "SELECT 1 FROM cirislens.trace_schemas WHERE version = $1",
            version,
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"Schema {version} not found")

        # Delete (cascade will remove field rules)
        await conn.execute(
            "DELETE FROM cirislens.trace_schemas WHERE version = $1",
            version,
        )

        # Refresh cache
        await refresh_schema_cache(conn)

    logger.info("SCHEMA_DELETED version=%s", version)
    return {"status": "deleted", "version": version}


async def startup_schema_cache(db_pool) -> None:
    """Initialize schema cache on application startup.

    Called from main application startup.
    """
    async with db_pool.acquire() as conn:
        await initialize_schema_cache(conn, LOCAL_SCHEMA_DIR)

    cache = get_schema_cache()
    if not cache.is_loaded:
        logger.warning("SCHEMA_CACHE_STARTUP_FAILED cache not loaded")
    else:
        logger.info(
            "SCHEMA_CACHE_STARTUP_COMPLETE schemas=%s",
            cache.schema_versions(),
        )
