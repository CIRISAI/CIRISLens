"""
CIRIS Covenant API V2 - Rust-powered trace ingestion

This module provides the FastAPI endpoints for trace ingestion using the
Rust-based cirislens_core module for high-performance processing.

Key differences from V1:
- Schema validation in Rust (not Python switch-case)
- Signature verification in Rust
- PII scrubbing in Rust
- Cache TTL with automatic refresh
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import asyncpg

# Import the Rust module - will fail gracefully if not built
try:
    import cirislens_core
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    cirislens_core = None

logger = logging.getLogger(__name__)

# Router for covenant endpoints
router = APIRouter(prefix="/api/v1/covenant", tags=["covenant-v2"])

# Cache refresh lock to prevent concurrent reloads
_cache_refresh_lock = asyncio.Lock()


def get_db_pool() -> asyncpg.Pool | None:
    """Get the database pool from main module."""
    import main
    return main.db_pool


# =============================================================================
# Pydantic Models (matching V1 format for backwards compatibility)
# =============================================================================


class TraceComponent(BaseModel):
    """A component within a trace event."""
    event_type: str
    timestamp: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None
    signature_key_id: str | None = None


class CovenantTrace(BaseModel):
    """Complete signed reasoning trace from an agent."""
    trace_id: str
    thought_id: str | None = None
    task_id: str | None = None
    agent_id_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    components: list[TraceComponent]
    signature: str  # Base64-encoded Ed25519 signature
    signature_key_id: str  # e.g., "agent-xxx"


class CovenantTraceEvent(BaseModel):
    """Wrapper for a trace event."""
    event_type: str = "complete_trace"
    trace: CovenantTrace


class CorrelationMetadata(BaseModel):
    """Optional metadata for correlation analysis."""
    deployment_region: str | None = None
    deployment_type: str | None = None
    agent_role: str | None = None
    agent_template: str | None = None


class CovenantEventsRequest(BaseModel):
    """Request body for trace ingestion."""
    events: list[CovenantTraceEvent]
    batch_timestamp: datetime
    consent_timestamp: datetime
    trace_level: str = "generic"
    correlation_metadata: CorrelationMetadata | None = None


# =============================================================================
# Cache Management
# =============================================================================


async def load_schemas_into_rust_cache(conn: asyncpg.Connection) -> int:
    """Load schemas from database into Rust cache."""
    if not RUST_AVAILABLE:
        return 0

    # Fetch schemas
    schema_rows = await conn.fetch("""
        SELECT version, description, status, signature_event_types
        FROM cirislens.trace_schemas
        WHERE status IN ('current', 'supported')
        ORDER BY
            CASE status
                WHEN 'current' THEN 1
                WHEN 'supported' THEN 2
                ELSE 3
            END,
            version DESC
    """)

    # Fetch field extraction rules
    field_rows = await conn.fetch("""
        SELECT schema_version, event_type, field_name, json_path, data_type, required, db_column
        FROM cirislens.trace_schema_fields
        WHERE schema_version IN (
            SELECT version FROM cirislens.trace_schemas
            WHERE status IN ('current', 'supported')
        )
    """)

    # Convert to tuples for Rust
    schemas = [
        (row['version'], row['description'] or '', row['status'], row['signature_event_types'] or [])
        for row in schema_rows
    ]
    fields = [
        (row['schema_version'], row['event_type'], row['field_name'],
         row['json_path'], row['data_type'], row['required'], row['db_column'] or '')
        for row in field_rows
    ]

    cirislens_core.load_schemas_from_db(schemas, fields)
    logger.info("Loaded %d schemas with %d field rules into Rust cache", len(schemas), len(fields))
    return len(schemas)


async def load_public_keys_into_rust_cache(conn: asyncpg.Connection) -> int:
    """Load public keys from database into Rust cache."""
    if not RUST_AVAILABLE:
        return 0

    key_rows = await conn.fetch("""
        SELECT key_id, public_key_base64
        FROM cirislens.covenant_public_keys
        WHERE revoked_at IS NULL
    """)

    keys = [(row['key_id'], row['public_key_base64']) for row in key_rows]
    cirislens_core.load_public_keys_from_db(keys)
    logger.info("Loaded %d public keys into Rust cache", len(keys))
    return len(keys)


async def ensure_caches_fresh(conn: asyncpg.Connection) -> None:
    """Check cache TTL and refresh if needed."""
    if not RUST_AVAILABLE:
        return

    schema_needs_refresh, keys_need_refresh, schema_age, key_age = cirislens_core.check_cache_status()

    if schema_needs_refresh or keys_need_refresh:
        async with _cache_refresh_lock:
            # Re-check after acquiring lock
            schema_needs_refresh, keys_need_refresh, _, _ = cirislens_core.check_cache_status()

            if schema_needs_refresh:
                logger.info("Schema cache TTL expired (age=%s), refreshing", schema_age)
                await load_schemas_into_rust_cache(conn)

            if keys_need_refresh:
                logger.info("Public key cache TTL expired (age=%s), refreshing", key_age)
                await load_public_keys_into_rust_cache(conn)


async def initialize_rust_caches() -> None:
    """Initialize Rust caches at startup."""
    if not RUST_AVAILABLE:
        logger.warning("Rust module not available - using Python fallback")
        return

    db_pool = get_db_pool()
    if db_pool is None:
        logger.error("Database not available for cache initialization")
        return

    async with db_pool.acquire() as conn:
        await load_schemas_into_rust_cache(conn)
        await load_public_keys_into_rust_cache(conn)


# =============================================================================
# Database Storage
# =============================================================================


async def store_production_trace(
    conn: asyncpg.Connection,
    trace_result: dict[str, Any],
    request: CovenantEventsRequest,
) -> None:
    """Store accepted trace in covenant_traces table."""
    metadata = trace_result.get('extracted_metadata', {})

    # Build the INSERT with all 79 columns from storage/queries.rs
    await conn.execute("""
        INSERT INTO cirislens.covenant_traces (
            trace_id, timestamp, trace_level, schema_version,
            batch_timestamp, consent_timestamp,
            signature, signature_key_id, signature_verified,
            pii_scrubbed, original_content_hash,
            thought_id, thought_type, thought_depth, task_id, task_description, started_at,
            agent_name, cognitive_state,
            csdma_plausibility, csdma_confidence, dsdma_alignment, dsdma_confidence,
            pdma_stakeholder_score, pdma_conflict_detected,
            idma_k_eff, idma_correlation_risk, idma_fragility_flag, idma_phase, idma_confidence,
            selected_action, action_rationale, aspdma_confidence,
            tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved,
            conscience_passed, conscience_override, conscience_override_reason,
            epistemic_humility, entropy_awareness, coherence_alignment,
            action_success, action_type, tokens_used, cost_usd, completed_at,
            positive_moment, models_used, api_bases_used,
            dma_results, aspdma_result, idma_result, tsaspdma_result,
            conscience_result, action_result,
            initial_context, system_snapshot, gathered_context
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
            $51, $52, $53, $54, $55, $56, $57, $58, $59, $60
        ) ON CONFLICT (trace_id) DO NOTHING
    """,
        trace_result['trace_id'],
        datetime.now(UTC),
        request.trace_level,
        trace_result.get('schema_version'),
        request.batch_timestamp,
        request.consent_timestamp,
        metadata.get('signature'),
        metadata.get('signature_key_id'),
        metadata.get('signature_verified', False),
        metadata.get('pii_scrubbed', False),
        metadata.get('original_content_hash'),
        metadata.get('thought_id'),
        metadata.get('thought_type'),
        metadata.get('thought_depth'),
        metadata.get('task_id'),
        metadata.get('task_description'),
        metadata.get('started_at'),
        metadata.get('agent_name'),
        metadata.get('cognitive_state'),
        metadata.get('csdma_plausibility'),
        metadata.get('csdma_confidence'),
        metadata.get('dsdma_alignment'),
        metadata.get('dsdma_confidence'),
        metadata.get('pdma_stakeholder_score'),
        metadata.get('pdma_conflict_detected'),
        metadata.get('idma_k_eff'),
        metadata.get('idma_correlation_risk'),
        metadata.get('idma_fragility_flag'),
        metadata.get('idma_phase'),
        metadata.get('idma_confidence'),
        metadata.get('selected_action'),
        metadata.get('action_rationale'),
        metadata.get('aspdma_confidence'),
        metadata.get('tool_name'),
        metadata.get('tool_parameters'),
        metadata.get('tsaspdma_reasoning'),
        metadata.get('tsaspdma_approved'),
        metadata.get('conscience_passed'),
        metadata.get('conscience_override'),
        metadata.get('conscience_override_reason'),
        metadata.get('epistemic_humility'),
        metadata.get('entropy_awareness'),
        metadata.get('coherence_alignment'),
        metadata.get('action_success'),
        metadata.get('action_type'),
        metadata.get('tokens_used'),
        metadata.get('cost_usd'),
        metadata.get('completed_at'),
        metadata.get('positive_moment'),
        metadata.get('models_used'),
        metadata.get('api_bases_used'),
        metadata.get('dma_results'),
        metadata.get('aspdma_result'),
        metadata.get('idma_result'),
        metadata.get('tsaspdma_result'),
        metadata.get('conscience_result'),
        metadata.get('action_result'),
        metadata.get('initial_context'),
        metadata.get('system_snapshot'),
        metadata.get('gathered_context'),
    )


async def store_mock_trace(
    conn: asyncpg.Connection,
    trace_result: dict[str, Any],
    request: CovenantEventsRequest,
) -> None:
    """Store mock trace in covenant_traces_mock table."""
    # Same structure as production, different table
    metadata = trace_result.get('extracted_metadata', {})

    await conn.execute("""
        INSERT INTO cirislens.covenant_traces_mock (
            trace_id, timestamp, trace_level, schema_version,
            batch_timestamp, consent_timestamp,
            thought_id, agent_name, selected_action, action_success
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (trace_id) DO NOTHING
    """,
        trace_result['trace_id'],
        datetime.now(UTC),
        request.trace_level,
        trace_result.get('schema_version'),
        request.batch_timestamp,
        request.consent_timestamp,
        metadata.get('thought_id'),
        metadata.get('agent_name'),
        metadata.get('selected_action'),
        metadata.get('action_success'),
    )


async def store_connectivity_event(
    conn: asyncpg.Connection,
    trace_result: dict[str, Any],
    request: CovenantEventsRequest,
) -> None:
    """Store connectivity event."""
    metadata = trace_result.get('extracted_metadata', {})

    await conn.execute("""
        INSERT INTO cirislens.connectivity_events (
            trace_id, timestamp, event_type, agent_id, agent_name,
            event_data, trace_level, consent_timestamp
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """,
        trace_result['trace_id'],
        datetime.now(UTC),
        metadata.get('event_type'),
        metadata.get('agent_id'),
        metadata.get('agent_name'),
        json.dumps(metadata.get('event_data', {})),
        request.trace_level,
        request.consent_timestamp,
    )


async def store_malformed_trace(
    conn: asyncpg.Connection,
    trace_result: dict[str, Any],
) -> None:
    """Store malformed trace metadata (never content)."""
    await conn.execute("""
        INSERT INTO cirislens.malformed_traces (
            record_id, trace_id, rejection_reason, severity,
            payload_sha256, signature_key_id
        ) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5)
    """,
        trace_result.get('trace_id'),
        trace_result.get('rejection_reason', 'Unknown'),
        'error',
        trace_result.get('content_hash', ''),
        trace_result.get('extracted_metadata', {}).get('signature_key_id'),
    )


async def store_batch_metadata(
    conn: asyncpg.Connection,
    request: CovenantEventsRequest,
    accepted: int,
    rejected: int,
    errors: list[str],
) -> None:
    """Store batch metadata."""
    correlation_json = None
    if request.correlation_metadata:
        correlation_json = json.dumps(request.correlation_metadata.model_dump(exclude_none=True))

    await conn.execute("""
        INSERT INTO cirislens.covenant_trace_batches (
            batch_timestamp, consent_timestamp,
            traces_received, traces_accepted, traces_rejected,
            rejection_reasons, trace_level, correlation_metadata
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """,
        request.batch_timestamp,
        request.consent_timestamp,
        len(request.events),
        accepted,
        rejected,
        json.dumps(errors) if errors else None,
        request.trace_level,
        correlation_json,
    )


# =============================================================================
# API Endpoint
# =============================================================================


@router.post("/events")
async def receive_covenant_events(request: CovenantEventsRequest) -> dict[str, Any]:
    """
    Receive and process covenant trace events.

    Uses Rust-based processing for:
    - Schema validation
    - Signature verification
    - Security sanitization
    - PII scrubbing
    - Field extraction
    - Routing decisions

    Python handles async database storage.
    """
    if not RUST_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Rust processing module not available"
        )

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Ensure caches are fresh (TTL check)
        await ensure_caches_fresh(conn)

        # Convert events to JSON strings for Rust
        # Note: events are wrapped in CovenantTraceEvent which has a .trace field
        events_json = [
            json.dumps({
                'trace_id': event.trace.trace_id,
                'thought_id': event.trace.thought_id,
                'task_id': event.trace.task_id,
                'agent_id_hash': event.trace.agent_id_hash,
                'started_at': event.trace.started_at,
                'completed_at': event.trace.completed_at,
                'signature': event.trace.signature,
                'signature_key_id': event.trace.signature_key_id,
                'components': [
                    {
                        'event_type': c.event_type,
                        'timestamp': c.timestamp,
                        'data': c.data,
                        'signature': c.signature,
                        'signature_key_id': c.signature_key_id,
                    }
                    for c in event.trace.components
                ]
            })
            for event in request.events
        ]

        # Process batch in Rust
        result = cirislens_core.process_trace_batch(
            events=events_json,
            batch_timestamp=request.batch_timestamp.isoformat(),
            consent_timestamp=request.consent_timestamp.isoformat() if request.consent_timestamp else None,
            trace_level=request.trace_level,
            correlation_metadata=json.dumps(request.correlation_metadata.model_dump(exclude_none=True)) if request.correlation_metadata else None,
        )

        accepted = 0
        rejected = 0
        errors = []

        # Store results based on routing decisions
        for trace_result in result['traces']:
            destination = trace_result.get('destination', 'unknown')

            try:
                if trace_result.get('accepted', False):
                    if destination == 'production':
                        await store_production_trace(conn, trace_result, request)
                        accepted += 1
                    elif destination == 'mock':
                        await store_mock_trace(conn, trace_result, request)
                        accepted += 1
                    elif destination == 'connectivity':
                        await store_connectivity_event(conn, trace_result, request)
                        accepted += 1
                else:
                    rejected += 1
                    reason = trace_result.get('rejection_reason', 'Unknown')
                    errors.append(f"{trace_result['trace_id']}: {reason}")

                    if destination == 'malformed':
                        await store_malformed_trace(conn, trace_result)

            except Exception as e:
                logger.error(
                    "Failed to store trace %s: %s",
                    trace_result.get('trace_id'),
                    e,
                    exc_info=True,
                )
                rejected += 1
                errors.append(f"{trace_result.get('trace_id')}: Storage error - {e}")

        # Store batch metadata
        await store_batch_metadata(conn, request, accepted, rejected, errors)

    logger.info(
        "Covenant events batch: received=%d accepted=%d rejected=%d",
        len(request.events),
        accepted,
        rejected,
    )

    response: dict[str, Any] = {
        "status": "ok" if rejected == 0 else "partial",
        "received": len(request.events),
        "accepted": accepted,
        "rejected": rejected,
        "batch_id": result.get('batch_id'),
    }

    if errors:
        response["errors"] = errors

    return response


# =============================================================================
# Cache Management Endpoints
# =============================================================================


@router.post("/cache/refresh")
async def refresh_caches() -> dict[str, Any]:
    """Force refresh of all Rust caches."""
    if not RUST_AVAILABLE:
        raise HTTPException(status_code=503, detail="Rust module not available")

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        cirislens_core.refresh_schema_cache()
        cirislens_core.refresh_public_key_cache()

        schemas_loaded = await load_schemas_into_rust_cache(conn)
        keys_loaded = await load_public_keys_into_rust_cache(conn)

    return {
        "status": "refreshed",
        "schemas_loaded": schemas_loaded,
        "keys_loaded": keys_loaded,
    }


@router.get("/cache/status")
async def get_cache_status() -> dict[str, Any]:
    """Get current cache status."""
    if not RUST_AVAILABLE:
        return {"status": "unavailable", "rust_available": False}

    schema_needs_refresh, keys_need_refresh, schema_age, key_age = cirislens_core.check_cache_status()

    return {
        "rust_available": True,
        "schemas": {
            "loaded": cirislens_core.get_loaded_schemas(),
            "needs_refresh": schema_needs_refresh,
            "age_seconds": schema_age,
        },
        "public_keys": {
            "count": cirislens_core.get_public_key_count(),
            "needs_refresh": keys_need_refresh,
            "age_seconds": key_age,
        },
    }
