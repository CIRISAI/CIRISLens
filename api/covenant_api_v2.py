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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
    import main  # noqa: PLC0415 - Intentional to avoid circular import
    return main.db_pool


# =============================================================================
# Pydantic Models (matching V1 format for backwards compatibility)
# =============================================================================


class TraceComponent(BaseModel):
    """Individual trace component (one of 6 types)."""
    component_type: str  # observation, context, rationale, conscience, action
    event_type: str  # THOUGHT_START, SNAPSHOT_AND_CONTEXT, etc.
    timestamp: str  # ISO timestamp
    data: dict[str, Any]


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


# WBD Deferral Events (simpler format sent by agents)
class WBDEvent(BaseModel):
    """A WBD deferral event from an agent."""
    event_type: str
    timestamp: str
    agent_id: str
    thought_id: str | None = None
    task_id: str | None = None
    reason: str | None = None
    defer_until: str | None = None


class WBDEventsRequest(BaseModel):
    """Request body for WBD deferral events."""
    events: list[WBDEvent]
    batch_timestamp: datetime
    consent_timestamp: datetime | None = None


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
    """Store accepted trace in covenant_traces table.

    Uses actual database column names that match the production schema.
    """
    metadata = trace_result.get('extracted_metadata', {})

    # Get signature info from the original event
    event = next(
        (e for e in request.events if e.trace.trace_id == trace_result['trace_id']),
        None
    )
    signature = event.trace.signature if event else metadata.get('signature')
    signature_key_id = event.trace.signature_key_id if event else metadata.get('signature_key_id')

    await conn.execute("""
        INSERT INTO cirislens.covenant_traces (
            trace_id, thought_id, task_id,
            agent_id_hash, agent_name,
            trace_type, cognitive_state, thought_type, thought_depth,
            started_at, completed_at,
            thought_start, snapshot_and_context, dma_results,
            aspdma_result, conscience_result, action_result,
            csdma_plausibility_score, dsdma_domain_alignment, dsdma_domain,
            pdma_stakeholders, pdma_conflicts,
            idma_k_eff, idma_correlation_risk, idma_fragility_flag, idma_phase,
            action_rationale,
            conscience_passed, action_was_overridden,
            entropy_level, coherence_level,
            entropy_passed, coherence_passed,
            optimization_veto_passed, epistemic_humility_passed,
            selected_action, action_success, processing_ms,
            tokens_input, tokens_output, tokens_total,
            cost_cents, llm_calls, models_used,
            signature, signature_key_id, signature_verified,
            consent_timestamp, timestamp, trace_level,
            has_positive_moment, has_execution_error, execution_time_ms,
            selection_confidence, is_recursive,
            idma_result, tsaspdma_result,
            tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
            $51, $52, $53, $54, $55, $56, $57, $58
        ) ON CONFLICT (trace_id) DO NOTHING
    """,
        trace_result['trace_id'],                         # $1
        metadata.get('thought_id'),                       # $2
        metadata.get('task_id'),                          # $3
        metadata.get('agent_id_hash'),                    # $4
        metadata.get('agent_name'),                       # $5
        metadata.get('trace_type'),                       # $6
        metadata.get('cognitive_state'),                  # $7
        metadata.get('thought_type'),                     # $8
        metadata.get('thought_depth'),                    # $9
        metadata.get('started_at'),                       # $10
        metadata.get('completed_at'),                     # $11
        json.dumps(metadata.get('thought_start')) if metadata.get('thought_start') else None,  # $12
        json.dumps(metadata.get('snapshot_and_context')) if metadata.get('snapshot_and_context') else None,  # $13
        json.dumps(metadata.get('dma_results')) if metadata.get('dma_results') else None,  # $14
        json.dumps(metadata.get('aspdma_result')) if metadata.get('aspdma_result') else None,  # $15
        json.dumps(metadata.get('conscience_result')) if metadata.get('conscience_result') else None,  # $16
        json.dumps(metadata.get('action_result')) if metadata.get('action_result') else None,  # $17
        metadata.get('csdma_plausibility_score'),         # $18
        metadata.get('dsdma_domain_alignment'),           # $19
        metadata.get('dsdma_domain'),                     # $20
        metadata.get('pdma_stakeholders'),                # $21
        metadata.get('pdma_conflicts'),                   # $22
        metadata.get('idma_k_eff'),                       # $23
        metadata.get('idma_correlation_risk'),            # $24
        metadata.get('idma_fragility_flag'),              # $25
        metadata.get('idma_phase'),                       # $26
        metadata.get('action_rationale'),                 # $27
        metadata.get('conscience_passed'),                # $28
        metadata.get('action_was_overridden'),            # $29
        metadata.get('entropy_level'),                    # $30
        metadata.get('coherence_level'),                  # $31
        metadata.get('entropy_passed'),                   # $32
        metadata.get('coherence_passed'),                 # $33
        metadata.get('optimization_veto_passed'),         # $34
        metadata.get('epistemic_humility_passed'),        # $35
        metadata.get('selected_action'),                  # $36
        metadata.get('action_success'),                   # $37
        metadata.get('processing_ms'),                    # $38
        metadata.get('tokens_input'),                     # $39
        metadata.get('tokens_output'),                    # $40
        metadata.get('tokens_total'),                     # $41
        metadata.get('cost_cents'),                       # $42
        metadata.get('llm_calls'),                        # $43
        metadata.get('models_used'),                      # $44
        signature,                                        # $45
        signature_key_id,                                 # $46
        metadata.get('signature_verified', False),        # $47
        request.consent_timestamp,                        # $48
        request.batch_timestamp,                          # $49
        request.trace_level,                              # $50
        metadata.get('has_positive_moment'),              # $51
        metadata.get('has_execution_error'),              # $52
        metadata.get('execution_time_ms'),                # $53
        metadata.get('selection_confidence'),             # $54
        metadata.get('is_recursive'),                     # $55
        json.dumps(metadata.get('idma_result')) if metadata.get('idma_result') else None,  # $56
        json.dumps(metadata.get('tsaspdma_result')) if metadata.get('tsaspdma_result') else None,  # $57
        metadata.get('tsaspdma_approved'),                # $58
    )


async def store_mock_trace(
    conn: asyncpg.Connection,
    trace_result: dict[str, Any],
    request: CovenantEventsRequest,
) -> None:
    """Store mock trace in covenant_traces_mock table.

    Uses same column schema as production traces.
    """
    metadata = trace_result.get('extracted_metadata', {})

    event = next(
        (e for e in request.events if e.trace.trace_id == trace_result['trace_id']),
        None
    )
    signature = event.trace.signature if event else metadata.get('signature')
    signature_key_id = event.trace.signature_key_id if event else metadata.get('signature_key_id')

    await conn.execute("""
        INSERT INTO cirislens.covenant_traces_mock (
            trace_id, thought_id, task_id,
            agent_id_hash, agent_name,
            trace_type, cognitive_state, thought_type, thought_depth,
            started_at, completed_at,
            thought_start, snapshot_and_context, dma_results,
            aspdma_result, conscience_result, action_result,
            csdma_plausibility_score, dsdma_domain_alignment, dsdma_domain,
            pdma_stakeholders, pdma_conflicts,
            idma_k_eff, idma_correlation_risk, idma_fragility_flag, idma_phase,
            action_rationale,
            conscience_passed, action_was_overridden,
            entropy_level, coherence_level,
            entropy_passed, coherence_passed,
            optimization_veto_passed, epistemic_humility_passed,
            selected_action, action_success, processing_ms,
            tokens_input, tokens_output, tokens_total,
            cost_cents, llm_calls, models_used,
            signature, signature_key_id, signature_verified,
            consent_timestamp, timestamp, trace_level,
            mock_models, mock_reason,
            has_positive_moment, has_execution_error, execution_time_ms,
            selection_confidence, is_recursive,
            idma_result, tsaspdma_result,
            tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
            $51, $52, $53, $54, $55, $56, $57, $58, $59, $60,
            $61, $62
        ) ON CONFLICT (trace_id) DO NOTHING
    """,
        trace_result['trace_id'],                         # $1
        metadata.get('thought_id'),                       # $2
        metadata.get('task_id'),                          # $3
        metadata.get('agent_id_hash'),                    # $4
        metadata.get('agent_name'),                       # $5
        metadata.get('trace_type'),                       # $6
        metadata.get('cognitive_state'),                  # $7
        metadata.get('thought_type'),                     # $8
        metadata.get('thought_depth'),                    # $9
        metadata.get('started_at'),                       # $10
        metadata.get('completed_at'),                     # $11
        json.dumps(metadata.get('thought_start')) if metadata.get('thought_start') else None,  # $12
        json.dumps(metadata.get('snapshot_and_context')) if metadata.get('snapshot_and_context') else None,  # $13
        json.dumps(metadata.get('dma_results')) if metadata.get('dma_results') else None,  # $14
        json.dumps(metadata.get('aspdma_result')) if metadata.get('aspdma_result') else None,  # $15
        json.dumps(metadata.get('conscience_result')) if metadata.get('conscience_result') else None,  # $16
        json.dumps(metadata.get('action_result')) if metadata.get('action_result') else None,  # $17
        metadata.get('csdma_plausibility_score'),         # $18
        metadata.get('dsdma_domain_alignment'),           # $19
        metadata.get('dsdma_domain'),                     # $20
        metadata.get('pdma_stakeholders'),                # $21
        metadata.get('pdma_conflicts'),                   # $22
        metadata.get('idma_k_eff'),                       # $23
        metadata.get('idma_correlation_risk'),            # $24
        metadata.get('idma_fragility_flag'),              # $25
        metadata.get('idma_phase'),                       # $26
        metadata.get('action_rationale'),                 # $27
        metadata.get('conscience_passed'),                # $28
        metadata.get('action_was_overridden'),            # $29
        metadata.get('entropy_level'),                    # $30
        metadata.get('coherence_level'),                  # $31
        metadata.get('entropy_passed'),                   # $32
        metadata.get('coherence_passed'),                 # $33
        metadata.get('optimization_veto_passed'),         # $34
        metadata.get('epistemic_humility_passed'),        # $35
        metadata.get('selected_action'),                  # $36
        metadata.get('action_success'),                   # $37
        metadata.get('processing_ms'),                    # $38
        metadata.get('tokens_input'),                     # $39
        metadata.get('tokens_output'),                    # $40
        metadata.get('tokens_total'),                     # $41
        metadata.get('cost_cents'),                       # $42
        metadata.get('llm_calls'),                        # $43
        metadata.get('models_used'),                      # $44
        signature,                                        # $45
        signature_key_id,                                 # $46
        metadata.get('signature_verified', False),        # $47
        request.consent_timestamp,                        # $48
        request.batch_timestamp,                          # $49
        request.trace_level,                              # $50
        metadata.get('mock_models'),                      # $51
        "models_used contains mock",                      # $52
        metadata.get('has_positive_moment'),              # $53
        metadata.get('has_execution_error'),              # $54
        metadata.get('execution_time_ms'),                # $55
        metadata.get('selection_confidence'),             # $56
        metadata.get('is_recursive'),                     # $57
        json.dumps(metadata.get('idma_result')) if metadata.get('idma_result') else None,  # $58
        json.dumps(metadata.get('tsaspdma_result')) if metadata.get('tsaspdma_result') else None,  # $59
        metadata.get('tool_name'),                        # $60
        json.dumps(metadata.get('tool_parameters')) if metadata.get('tool_parameters') else None,  # $61
        metadata.get('tsaspdma_approved'),                # $62
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
# API Endpoints
# =============================================================================


@router.post("/wbd/events")
async def receive_wbd_events(request: WBDEventsRequest) -> dict[str, Any]:
    """
    Receive WBD (Wisdom-Based Deferral) events from agents.

    These are simpler events indicating an agent deferred a decision.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    accepted = 0
    errors = []

    async with db_pool.acquire() as conn:
        for event in request.events:
            try:
                await conn.execute("""
                    INSERT INTO cirislens.wbd_deferrals (
                        agent_id, trigger_type, trigger_description,
                        thought_id, task_id, defer_until,
                        created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT DO NOTHING
                """,
                    event.agent_id,
                    event.event_type,
                    event.reason,
                    event.thought_id,
                    event.task_id,
                    event.defer_until,
                    datetime.fromisoformat(event.timestamp.replace('Z', '+00:00')) if event.timestamp else datetime.now(UTC),
                )
                accepted += 1
            except Exception as e:
                logger.error("Failed to store WBD event: %s", e)
                errors.append(str(e))

    logger.info("WBD events: received=%d accepted=%d", len(request.events), accepted)
    return {
        "status": "ok" if not errors else "partial",
        "received": len(request.events),
        "accepted": accepted,
        "errors": errors if errors else None,
    }


@router.post("/events")
async def receive_covenant_events(request: Request) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
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
    # Parse and validate request manually to detect event type
    body = await request.body()
    try:
        raw_data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse request JSON: %s", e)
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {e}") from e

    # Detect event type from first event
    if raw_data.get('events'):
        first_event = raw_data['events'][0]

        # Check if this is a WBD deferral event (has defer_until or reason, no trace)
        if 'defer_until' in first_event or ('reason' in first_event and 'trace' not in first_event):
            logger.info("Detected WBD deferral events, routing to WBD handler")
            try:
                wbd_request = WBDEventsRequest.model_validate(raw_data)
            except Exception as e:
                logger.error("WBD validation failed: %s", e)
                raise HTTPException(status_code=422, detail=str(e)) from e

            # Handle WBD events inline
            db_pool = get_db_pool()
            if db_pool is None:
                raise HTTPException(status_code=503, detail="Database not available")

            accepted = 0
            errors = []
            async with db_pool.acquire() as conn:
                for event in wbd_request.events:
                    try:
                        await conn.execute("""
                            INSERT INTO cirislens.wbd_deferrals (
                                agent_id, trigger_type, trigger_description,
                                thought_id, task_id, defer_until,
                                created_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT DO NOTHING
                        """,
                            event.agent_id,
                            event.event_type,
                            event.reason,
                            event.thought_id,
                            event.task_id,
                            event.defer_until,
                            datetime.fromisoformat(event.timestamp.replace('Z', '+00:00')) if event.timestamp else datetime.now(UTC),
                        )
                        accepted += 1
                    except Exception as e:
                        logger.error("Failed to store WBD event: %s", e)
                        errors.append(str(e))

            logger.info("WBD events: received=%d accepted=%d", len(wbd_request.events), accepted)
            return {
                "status": "ok" if not errors else "partial",
                "received": len(wbd_request.events),
                "accepted": accepted,
                "errors": errors if errors else None,
            }

    # Validate as trace events
    try:
        validated_request = CovenantEventsRequest.model_validate(raw_data)
    except Exception as e:
        logger.error("Pydantic validation failed: %s", e)
        raise HTTPException(status_code=422, detail=str(e)) from e

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
                        'component_type': c.component_type,
                        'event_type': c.event_type,
                        'timestamp': c.timestamp,
                        'data': c.data,
                    }
                    for c in event.trace.components
                ]
            })
            for event in validated_request.events
        ]

        # Process batch in Rust
        result = cirislens_core.process_trace_batch(
            events=events_json,
            batch_timestamp=validated_request.batch_timestamp.isoformat(),
            consent_timestamp=validated_request.consent_timestamp.isoformat() if validated_request.consent_timestamp else None,
            trace_level=validated_request.trace_level,
            correlation_metadata=json.dumps(validated_request.correlation_metadata.model_dump(exclude_none=True)) if validated_request.correlation_metadata else None,
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
                        await store_production_trace(conn, trace_result, validated_request)
                        accepted += 1
                    elif destination == 'mock':
                        await store_mock_trace(conn, trace_result, validated_request)
                        accepted += 1
                    elif destination == 'connectivity':
                        await store_connectivity_event(conn, trace_result, validated_request)
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
        await store_batch_metadata(conn, validated_request, accepted, rejected, errors)

    logger.info(
        "Covenant events batch: received=%d accepted=%d rejected=%d",
        len(validated_request.events),
        accepted,
        rejected,
    )

    response: dict[str, Any] = {
        "status": "ok" if rejected == 0 else "partial",
        "received": len(validated_request.events),
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
