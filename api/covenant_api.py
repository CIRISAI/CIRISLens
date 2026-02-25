"""
CIRIS Covenant API - Backward-compatible aliases

DEPRECATED: This module provides backward-compatible aliases for the renamed
Accord API. New code should use accord_api instead.

The /api/v1/covenant/* routes are maintained for backward compatibility
but will be removed in a future version.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

# Import everything from the new accord module
try:
    from accord_api import (
        AccessLevel,
        AcknowledgeAlertRequest,
        AgentComplianceStatus,
        CoherenceRatchetAlertResponse,
        CorrelationMetadata,
        CreatorLedgerEntry,
        CreatorLedgerResponse,
        PartnerAccessRequest,
        PDMAEventCreate,
        PDMAEventResponse,
        PDMAOutcomeUpdate,
        PublicKeyCreate,
        PublicSampleRequest,
        ResolveAlertRequest,
        RunDetectionResponse,
        SunsetLedgerEntry,
        SunsetLedgerResponse,
        SunsetProgressUpdate,
        TraceAccessContext,
        # Unchanged models
        TraceComponent,
        TraceRepositoryResponse,
        TraceStatisticsResponse,
        WBDDeferralCreate,
        WBDDeferralResponse,
        WBDResolution,
        _is_mock_trace,
        _parse_timestamp,
        acknowledge_alert,
        build_access_scope_filter,
        compute_entry_hash,
        # Route handlers - Creator Ledger
        create_creator_ledger_entry,
        # Route handlers - PDMA
        create_pdma_event,
        # Route handlers - Sunset
        create_sunset_entry,
        # Route handlers - WBD
        create_wbd_deferral,
        extract_trace_metadata,
        filter_trace_fields,
        get_coherence_ratchet_stats,
        # Route handlers - Compliance
        get_compliance_status,
        get_compliance_summary,
        get_db_pool,
        get_repository_statistics,
        get_repository_trace,
        get_scheduler,
        # Route handlers - Coherence Ratchet
        list_coherence_ratchet_alerts,
        list_creator_ledger,
        list_pdma_events,
        list_public_keys,
        # Route handlers - Repository
        list_repository_traces,
        list_sunset_entries,
        list_wbd_deferrals,
        load_public_keys,
        # Functions
        receive_accord_events,
        # Route handlers - Public Keys
        register_public_key,
        resolve_alert,
        resolve_wbd_deferral,
        run_coherence_ratchet_detection,
        set_scheduler,
        set_trace_partner_access,
        set_trace_public_sample,
        update_pdma_outcomes,
        update_sunset_progress,
        verify_trace_signature,
    )
    from accord_api import (
        AccordEventsRequest as CovenantEventsRequest,
    )
    from accord_api import (
        AccordEventsResponse as CovenantEventsResponse,
    )
    from accord_api import (
        # Models (with old names as aliases)
        AccordTrace as CovenantTrace,
    )
    from accord_api import (
        AccordTraceEvent as CovenantTraceEvent,
    )
except ImportError:
    from api.accord_api import (
        AccessLevel,
        AcknowledgeAlertRequest,
        AgentComplianceStatus,
        CoherenceRatchetAlertResponse,
        CorrelationMetadata,
        CreatorLedgerEntry,
        CreatorLedgerResponse,
        PartnerAccessRequest,
        PDMAEventCreate,
        PDMAEventResponse,
        PDMAOutcomeUpdate,
        PublicKeyCreate,
        PublicSampleRequest,
        ResolveAlertRequest,
        RunDetectionResponse,
        SunsetLedgerEntry,
        SunsetLedgerResponse,
        SunsetProgressUpdate,
        TraceAccessContext,
        # Unchanged models
        TraceComponent,
        TraceRepositoryResponse,
        TraceStatisticsResponse,
        WBDDeferralCreate,
        WBDDeferralResponse,
        WBDResolution,
        _is_mock_trace,
        _parse_timestamp,
        acknowledge_alert,
        build_access_scope_filter,
        compute_entry_hash,
        # Route handlers - Creator Ledger
        create_creator_ledger_entry,
        # Route handlers - PDMA
        create_pdma_event,
        # Route handlers - Sunset
        create_sunset_entry,
        # Route handlers - WBD
        create_wbd_deferral,
        extract_trace_metadata,
        filter_trace_fields,
        get_coherence_ratchet_stats,
        # Route handlers - Compliance
        get_compliance_status,
        get_compliance_summary,
        get_db_pool,
        get_repository_statistics,
        get_repository_trace,
        get_scheduler,
        # Route handlers - Coherence Ratchet
        list_coherence_ratchet_alerts,
        list_creator_ledger,
        list_pdma_events,
        list_public_keys,
        # Route handlers - Repository
        list_repository_traces,
        list_sunset_entries,
        list_wbd_deferrals,
        load_public_keys,
        # Functions
        receive_accord_events,
        # Route handlers - Public Keys
        register_public_key,
        resolve_alert,
        resolve_wbd_deferral,
        run_coherence_ratchet_detection,
        set_scheduler,
        set_trace_partner_access,
        set_trace_public_sample,
        update_pdma_outcomes,
        update_sunset_progress,
        verify_trace_signature,
    )
    from api.accord_api import (
        AccordEventsRequest as CovenantEventsRequest,
    )
    from api.accord_api import (
        AccordEventsResponse as CovenantEventsResponse,
    )
    from api.accord_api import (
        # Models (with old names as aliases)
        AccordTrace as CovenantTrace,
    )
    from api.accord_api import (
        AccordTraceEvent as CovenantTraceEvent,
    )

logger = logging.getLogger(__name__)

# Backward-compatible router for /api/v1/covenant/* routes
router = APIRouter(prefix="/api/v1/covenant", tags=["covenant-deprecated"])


# =============================================================================
# Trace Events (deprecated - use /api/v1/accord/events)
# =============================================================================


@router.post("/events")
async def receive_covenant_events(request: CovenantEventsRequest) -> dict[str, Any]:
    """
    DEPRECATED: Use /api/v1/accord/events instead.

    Backward-compatible endpoint that forwards to the accord API.
    """
    logger.warning("DEPRECATED: /api/v1/covenant/events called - use /api/v1/accord/events")
    return await receive_accord_events(request)


# =============================================================================
# WBD Endpoints (deprecated)
# =============================================================================


@router.post("/wbd/deferrals")
async def create_covenant_wbd_deferral(deferral: WBDDeferralCreate) -> WBDDeferralResponse:
    """DEPRECATED: Use /api/v1/accord/wbd/deferrals instead."""
    return await create_wbd_deferral(deferral)


@router.get("/wbd/deferrals")
async def list_covenant_wbd_deferrals(
    agent_id: str | None = None,
    resolved: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/wbd/deferrals instead."""
    return await list_wbd_deferrals(agent_id=agent_id, resolved=resolved, limit=limit, offset=offset)


@router.put("/wbd/deferrals/{deferral_id}/resolve")
async def resolve_covenant_wbd_deferral(deferral_id: str, resolution: WBDResolution) -> WBDDeferralResponse:
    """DEPRECATED: Use /api/v1/accord/wbd/deferrals/{id}/resolve instead."""
    return await resolve_wbd_deferral(deferral_id, resolution)


# =============================================================================
# PDMA Endpoints (deprecated)
# =============================================================================


@router.post("/pdma/events")
async def create_covenant_pdma_event(event: PDMAEventCreate) -> PDMAEventResponse:
    """DEPRECATED: Use /api/v1/accord/pdma/events instead."""
    return await create_pdma_event(event)


@router.get("/pdma/events")
async def list_covenant_pdma_events(
    agent_id: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/pdma/events instead."""
    return await list_pdma_events(agent_id=agent_id, domain=domain, limit=limit, offset=offset)


@router.put("/pdma/events/{event_id}/outcomes")
async def update_covenant_pdma_outcomes(event_id: str, outcome: PDMAOutcomeUpdate) -> PDMAEventResponse:
    """DEPRECATED: Use /api/v1/accord/pdma/events/{id}/outcomes instead."""
    return await update_pdma_outcomes(event_id, outcome)


# =============================================================================
# Creator Ledger Endpoints (deprecated)
# =============================================================================


@router.post("/creator-ledger")
async def create_covenant_creator_ledger_entry(entry: CreatorLedgerEntry) -> CreatorLedgerResponse:
    """DEPRECATED: Use /api/v1/accord/creator-ledger instead."""
    return await create_creator_ledger_entry(entry)


@router.get("/creator-ledger")
async def list_covenant_creator_ledger(
    agent_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/creator-ledger instead."""
    return await list_creator_ledger(agent_id=agent_id, limit=limit, offset=offset)


# =============================================================================
# Sunset Ledger Endpoints (deprecated)
# =============================================================================


@router.post("/sunset-ledger")
async def create_covenant_sunset_entry(entry: SunsetLedgerEntry) -> SunsetLedgerResponse:
    """DEPRECATED: Use /api/v1/accord/sunset-ledger instead."""
    return await create_sunset_entry(entry)


@router.get("/sunset-ledger")
async def list_covenant_sunset_entries(
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/sunset-ledger instead."""
    return await list_sunset_entries(agent_id=agent_id, status=status, limit=limit, offset=offset)


@router.put("/sunset-ledger/{entry_id}/progress")
async def update_covenant_sunset_progress(entry_id: str, progress: SunsetProgressUpdate) -> SunsetLedgerResponse:
    """DEPRECATED: Use /api/v1/accord/sunset-ledger/{id}/progress instead."""
    return await update_sunset_progress(entry_id, progress)


# =============================================================================
# Compliance Endpoints (deprecated)
# =============================================================================


@router.get("/compliance/status")
async def get_covenant_compliance_status(agent_id: str) -> AgentComplianceStatus:
    """DEPRECATED: Use /api/v1/accord/compliance/status instead."""
    return await get_compliance_status(agent_id)


@router.get("/compliance/summary")
async def get_covenant_compliance_summary() -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/compliance/summary instead."""
    return await get_compliance_summary()


# =============================================================================
# Public Keys Endpoints (deprecated)
# =============================================================================


@router.post("/public-keys")
async def register_covenant_public_key(key_data: PublicKeyCreate) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/public-keys instead."""
    return await register_public_key(key_data)


@router.get("/public-keys")
async def list_covenant_public_keys() -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/public-keys instead."""
    return await list_public_keys()


# =============================================================================
# Repository Endpoints (deprecated)
# =============================================================================


@router.get("/repository/traces")
async def list_covenant_repository_traces(
    limit: int = 100,
    offset: int = 0,
    agent_name: str | None = None,
    trace_level: str | None = None,
    access: TraceAccessContext | None = None,
) -> TraceRepositoryResponse:
    """DEPRECATED: Use /api/v1/accord/repository/traces instead."""
    return await list_repository_traces(limit=limit, offset=offset, agent_name=agent_name, trace_level=trace_level, access=access)


@router.get("/repository/traces/{trace_id}")
async def get_covenant_repository_trace(
    trace_id: str,
    access: TraceAccessContext | None = None,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/repository/traces/{id} instead."""
    return await get_repository_trace(trace_id, access=access)


@router.get("/repository/statistics")
async def get_covenant_repository_statistics(
    access: TraceAccessContext | None = None,
) -> TraceStatisticsResponse:
    """DEPRECATED: Use /api/v1/accord/repository/statistics instead."""
    return await get_repository_statistics(access=access)


@router.put("/repository/traces/{trace_id}/public-sample")
async def set_covenant_trace_public_sample(
    trace_id: str,
    request: PublicSampleRequest,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/repository/traces/{id}/public-sample instead."""
    return await set_trace_public_sample(trace_id, request)


@router.put("/repository/traces/{trace_id}/partner-access")
async def set_covenant_trace_partner_access(
    trace_id: str,
    request: PartnerAccessRequest,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/repository/traces/{id}/partner-access instead."""
    return await set_trace_partner_access(trace_id, request)


# =============================================================================
# Coherence Ratchet Endpoints (deprecated)
# =============================================================================


@router.get("/coherence-ratchet/alerts")
async def list_covenant_coherence_ratchet_alerts(
    status: str | None = None,
    severity: str | None = None,
    agent_name: str | None = None,
    detection_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/coherence-ratchet/alerts instead."""
    return await list_coherence_ratchet_alerts(
        status=status, severity=severity, agent_name=agent_name,
        detection_type=detection_type, limit=limit, offset=offset
    )


@router.post("/coherence-ratchet/run")
async def run_covenant_coherence_ratchet_detection() -> RunDetectionResponse:
    """DEPRECATED: Use /api/v1/accord/coherence-ratchet/run instead."""
    return await run_coherence_ratchet_detection()


@router.put("/coherence-ratchet/alerts/{alert_id}/acknowledge")
async def acknowledge_covenant_alert(
    alert_id: str,
    request: AcknowledgeAlertRequest,
) -> CoherenceRatchetAlertResponse:
    """DEPRECATED: Use /api/v1/accord/coherence-ratchet/alerts/{id}/acknowledge instead."""
    return await acknowledge_alert(alert_id, request)


@router.put("/coherence-ratchet/alerts/{alert_id}/resolve")
async def resolve_covenant_alert(
    alert_id: str,
    request: ResolveAlertRequest,
) -> CoherenceRatchetAlertResponse:
    """DEPRECATED: Use /api/v1/accord/coherence-ratchet/alerts/{id}/resolve instead."""
    return await resolve_alert(alert_id, request)


@router.get("/coherence-ratchet/stats")
async def get_covenant_coherence_ratchet_stats(hours: int = 168) -> dict[str, Any]:
    """DEPRECATED: Use /api/v1/accord/coherence-ratchet/stats instead."""
    return await get_coherence_ratchet_stats(hours=hours)


# Re-export models for backward compatibility
__all__ = [
    # Deprecated aliases
    "CovenantTrace",
    "CovenantTraceEvent",
    "CovenantEventsRequest",
    "CovenantEventsResponse",
    # Shared models
    "TraceComponent",
    "CorrelationMetadata",
    "WBDDeferralCreate",
    "WBDDeferralResponse",
    "WBDResolution",
    "PDMAEventCreate",
    "PDMAEventResponse",
    "PDMAOutcomeUpdate",
    "CreatorLedgerEntry",
    "CreatorLedgerResponse",
    "SunsetLedgerEntry",
    "SunsetLedgerResponse",
    "SunsetProgressUpdate",
    "AgentComplianceStatus",
    "PublicKeyCreate",
    "AccessLevel",
    "TraceAccessContext",
    "PublicSampleRequest",
    "PartnerAccessRequest",
    "TraceRepositoryResponse",
    "TraceStatisticsResponse",
    "CoherenceRatchetAlertResponse",
    "RunDetectionResponse",
    "AcknowledgeAlertRequest",
    "ResolveAlertRequest",
    # Router
    "router",
    # Functions
    "receive_covenant_events",
    "extract_trace_metadata",
    "verify_trace_signature",
    "load_public_keys",
    "compute_entry_hash",
    "build_access_scope_filter",
    "filter_trace_fields",
    "get_db_pool",
    "get_scheduler",
    "set_scheduler",
    "_is_mock_trace",
    "_parse_timestamp",
]
