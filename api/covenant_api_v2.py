"""
CIRIS Covenant API V2 - Backward-compatible aliases

DEPRECATED: This module provides backward-compatible aliases for the renamed
Accord API. New code should use accord_api_v2 instead.

The /api/v1/covenant/* routes are maintained for backward compatibility
but will be removed in a future version.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from accord_api import (
    list_repository_traces,  # moved to v1 in stage-2 of CIRISLens#10 / CIRISPersist#23
)
from accord_api_v2 import (
    RUST_AVAILABLE,
    CorrelationMetadata,
    TraceComponent,
    get_db_pool,
    list_public_keys,
    # Functions
    receive_accord_events,
    register_public_key,
    store_batch_metadata,
    store_connectivity_event,
    store_mock_trace,
    store_production_trace,
)
from accord_api_v2 import (
    AccordEventsRequest as CovenantEventsRequest,
)

# Import everything from the new accord module
from accord_api_v2 import (
    # Models (with old names as aliases)
    AccordTrace as CovenantTrace,
)
from accord_api_v2 import (
    AccordTraceEvent as CovenantTraceEvent,
)

logger = logging.getLogger(__name__)

# Backward-compatible router for /api/v1/covenant/* routes
router = APIRouter(prefix="/api/v1/covenant", tags=["covenant-v2-deprecated"])


@router.post("/events")
async def receive_covenant_events(request: Request) -> dict[str, Any]:
    """
    DEPRECATED: Use /api/v1/accord/events instead.

    Backward-compatible endpoint that forwards to the accord API.
    """
    logger.warning("DEPRECATED: /api/v1/covenant/events called - use /api/v1/accord/events")
    return await receive_accord_events(request)


@router.get("/repository/traces")
async def list_covenant_traces(
    cursor: str | None = None,
    limit: int = 100,
    agent_id_hash: str | None = None,
    agent_name: str | None = None,
    deployment_domain: str | None = None,
) -> dict[str, Any]:
    """
    DEPRECATED: Use /api/v1/accord/repository/traces instead.

    Surface re-aligned in stage-2 of the persist v0.5.0 migration
    (CIRISPersist#23 / CIRISLens#10). The legacy ``offset`` /
    ``trace_level`` parameters no longer apply — see the accord-side
    endpoint docstring for the deferral details.
    """
    return await list_repository_traces(
        cursor=cursor,
        limit=limit,
        agent_id_hash=agent_id_hash,
        agent_name=agent_name,
        deployment_domain=deployment_domain,
    )


@router.post("/public-keys")
async def register_covenant_public_key(request: Request) -> dict[str, Any]:
    """
    DEPRECATED: Use /api/v1/accord/public-keys instead.
    """
    return await register_public_key(request)


@router.get("/public-keys")
async def list_covenant_public_keys() -> dict[str, Any]:
    """
    DEPRECATED: Use /api/v1/accord/public-keys instead.
    """
    return await list_public_keys()


# Re-export models for backward compatibility
__all__ = [
    # Deprecated aliases
    "CovenantTrace",
    "CovenantTraceEvent",
    "CovenantEventsRequest",
    # Shared models
    "TraceComponent",
    "CorrelationMetadata",
    # Router
    "router",
    # Functions
    "receive_covenant_events",
    "store_production_trace",
    "store_mock_trace",
    "store_connectivity_event",
    "store_batch_metadata",
    "get_db_pool",
    "RUST_AVAILABLE",
]
