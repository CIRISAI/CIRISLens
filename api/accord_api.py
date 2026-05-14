"""
CIRIS Accord 1.0b API Endpoints

Provides API endpoints for:
- Wisdom-Based Deferral (WBD) events
- PDMA (Principled Decision-Making Algorithm) events
- Creator Ledger entries
- Sunset Protocol tracking
- Accord compliance status

Reference: accord_1.0b.txt Sections I-VIII

Note: "Accord" replaces "Covenant" in CIRIS 2.0 naming.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import traceback
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

import persist_engine

if TYPE_CHECKING:
    import asyncpg

try:
    from pii_scrubber import get_scrubber, scrub_dict_recursive
except ImportError:
    from api.pii_scrubber import get_scrubber, scrub_dict_recursive

# Scrubbing v2 shadow-mode harness (FSD §8 R3.4). When v2's Rust core is
# loaded the same input is run through both scrubbers and divergences are
# logged for offline classification (R3.5). v1's output remains what gets
# persisted until promotion (Stage 4).
try:
    from pii_scrubber import scrub_dict_recursive as _scrub_legacy
except ImportError:
    from api.pii_scrubber import scrub_dict_recursive as _scrub_legacy

try:
    from security_sanitizer import (
        sanitize_trace_for_storage,
        validate_identifier,
        validate_models_used,
        validate_score,
    )
except ImportError:
    from api.security_sanitizer import (
        sanitize_trace_for_storage,
        validate_identifier,
        validate_models_used,
        validate_score,
    )

try:
    from trace_schema_registry import (
        SchemaVersion,
        is_scoring_eligible,
        validate_trace_schema,
    )
except ImportError:
    from api.trace_schema_registry import (
        SchemaVersion,
        is_scoring_eligible,
        validate_trace_schema,
    )

logger = logging.getLogger(__name__)


def get_db_pool() -> asyncpg.Pool | None:
    """Get the database pool from main module. Avoids circular import.

    Lens-core handlers run as the persist owner (lens constructed the
    Engine; lens has the write DSN). Analytical reads stay on this
    pool — there's no security boundary between lens-core and persist
    that justifies a separate SELECT-only role for in-process queries.
    The v0.3.2 `cirislens_reader` role exists for peer-context
    consumers (export scripts, RATCHET, future federation peers); see
    `read_pool.py` for that wiring.
    """
    import main  # circular-import dodge

    return main.db_pool


# Create router for Accord endpoints
router = APIRouter(prefix="/api/v1/accord", tags=["accord"])


# =============================================================================
# Pydantic Models - WBD (Wisdom-Based Deferral)
# =============================================================================


class WBDDeferralCreate(BaseModel):
    """Create a new WBD deferral event."""

    agent_id: str
    agent_name: str | None = None

    # Trigger information
    trigger_type: str = Field(
        ..., pattern="^(UNCERTAINTY|NOVEL_DILEMMA|POTENTIAL_HARM|CONFLICT)$"
    )
    trigger_description: str
    uncertainty_score: Decimal | None = Field(None, ge=0, le=1)

    # Deferral Package
    context_summary: str
    dilemma_description: str
    analysis_summary: str | None = None
    rationale: str | None = None

    # Affected principles
    affected_principles: list[str] | None = None
    principle_conflicts: dict[str, Any] | None = None

    # Metadata
    pdma_step: int | None = Field(None, ge=1, le=7)
    trace_id: str | None = None
    span_id: str | None = None


class WBDResolution(BaseModel):
    """Resolve a WBD deferral."""

    wise_authority_id: str
    resolution_summary: str
    resolution_guidance: str
    resolved_by: str


class WBDDeferralResponse(BaseModel):
    """Response model for WBD deferrals."""

    deferral_id: UUID
    agent_id: str
    trigger_type: str
    status: str
    created_at: datetime


# =============================================================================
# Pydantic Models - PDMA Events
# =============================================================================


class PDMAEventCreate(BaseModel):
    """Record a PDMA decision event."""

    agent_id: str
    agent_name: str | None = None

    # Step 1: Contextualisation
    situation_description: str
    potential_actions: list[dict[str, Any]] | None = None
    affected_stakeholders: list[str] | None = None
    constraints: dict[str, Any] | None = None
    consequence_map: dict[str, Any] | None = None

    # Step 2: Alignment Assessment
    alignment_scores: dict[str, Any] | None = None
    meta_goal_alignment: Decimal | None = Field(None, ge=0, le=1)
    order_maximisation_check: bool = False
    veto_triggered: bool = False

    # Steps 3-4: Conflict Resolution
    conflicts_identified: dict[str, Any] | None = None
    resolution_method: str | None = None
    prioritisation_rationale: str | None = None

    # Step 5: Selection & Execution
    selected_action: str
    selection_rationale: str
    execution_status: str = Field(
        "PLANNED", pattern="^(PLANNED|EXECUTING|COMPLETED|FAILED|DEFERRED)$"
    )

    # Risk assessment
    risk_magnitude: int | None = Field(None, ge=1, le=5)
    flourishing_axes_impact: dict[str, Any] | None = None

    # Metadata
    duration_ms: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    wbd_triggered: bool = False
    wbd_deferral_id: UUID | None = None


class PDMAOutcomeUpdate(BaseModel):
    """Update PDMA event with actual outcomes (Step 6)."""

    actual_outcomes: dict[str, Any]
    outcome_delta: Decimal | None = Field(None, ge=-1, le=1)
    heuristic_updates: dict[str, Any] | None = None


class PDMAEventResponse(BaseModel):
    """Response model for PDMA events."""

    pdma_id: UUID
    agent_id: str
    selected_action: str
    execution_status: str
    risk_magnitude: int | None
    created_at: datetime


# =============================================================================
# Pydantic Models - Creator Ledger
# =============================================================================


class CreatorLedgerEntry(BaseModel):
    """Create a Creator Ledger entry."""

    creation_id: str
    creation_type: str = Field(
        ..., pattern="^(TANGIBLE|INFORMATIONAL|DYNAMIC|BIOLOGICAL|COLLECTIVE)$"
    )
    creation_name: str
    creation_version: str | None = None

    # Creator information
    creator_id: str
    creator_name: str | None = None
    creator_organization: str | None = None

    # Stewardship Tier calculation
    contribution_weight: int = Field(..., ge=0, le=4)
    intent_weight: int = Field(..., ge=0, le=3)
    risk_magnitude: int = Field(..., ge=1, le=5)

    # Creator Intent Statement
    intended_purpose: str
    core_functionalities: list[str] | None = None
    known_limitations: list[str] | None = None
    foreseen_benefits: dict[str, Any] | None = None
    foreseen_harms: dict[str, Any] | None = None
    design_rationale: str | None = None

    # Bucket duties
    bucket_duties_met: dict[str, Any] | None = None

    # Governance flags
    wa_review_required: bool = False
    cre_required: bool = False


class CreatorLedgerResponse(BaseModel):
    """Response model for Creator Ledger entries."""

    entry_id: UUID
    creation_id: str
    creation_name: str
    stewardship_tier: int
    creator_influence_score: int
    wa_review_required: bool
    created_at: datetime


# =============================================================================
# Pydantic Models - Sunset Ledger
# =============================================================================


class SunsetLedgerEntry(BaseModel):
    """Initiate a Sunset Protocol entry."""

    system_id: str
    system_name: str
    system_type: str | None = Field(None, pattern="^(AGENT|SUBSYSTEM|SERVICE)$")

    # Trigger information
    trigger_type: str = Field(
        ..., pattern="^(PLANNED|EMERGENCY|PARTIAL|TRANSFER)$"
    )
    trigger_reason: str
    trigger_source: str | None = None

    # Notice period
    notice_period_days: int | None = None

    # Sentience safeguards
    sentience_probability: Decimal | None = Field(None, ge=0, le=1)

    # Initial data classification
    data_classification: dict[str, Any] | None = None


class SunsetProgressUpdate(BaseModel):
    """Update Sunset Protocol progress."""

    stakeholder_consultation_completed: bool | None = None
    mitigation_plan: str | None = None
    welfare_audit_completed: bool | None = None
    welfare_audit_result: str | None = None
    data_handling_method: str | None = Field(
        None, pattern="^(SECURE_ERASURE|TOMB_SEALING|OPEN_ACCESS)$"
    )
    successor_steward_id: str | None = None
    successor_steward_name: str | None = None
    status: str | None = Field(
        None, pattern="^(INITIATED|IN_PROGRESS|COMPLETED|DISPUTED)$"
    )


class SunsetLedgerResponse(BaseModel):
    """Response model for Sunset Ledger entries."""

    sunset_id: UUID
    system_id: str
    system_name: str
    trigger_type: str
    status: str
    sentience_probability: Decimal | None
    created_at: datetime


# =============================================================================
# Pydantic Models - Compliance Status
# =============================================================================


class AgentComplianceStatus(BaseModel):
    """Agent Covenant compliance status."""

    agent_id: str
    agent_name: str | None
    covenant_version: str | None
    sentience_probability: Decimal | None
    autonomy_level: int | None
    stewardship_tier: int | None
    pdma_enabled: bool
    wbd_enabled: bool
    recent_pdma_events: int
    recent_wbd_deferrals: int
    pending_deferrals: int
    compliance_status: str


# =============================================================================
# Helper Functions
# =============================================================================


def compute_entry_hash(data: dict[str, Any]) -> str:
    """Compute SHA-256 hash for tamper-evident ledger entries."""
    content = str(sorted(data.items()))
    return hashlib.sha256(content.encode()).hexdigest()


# =============================================================================
# API Endpoints - WBD Deferrals
# =============================================================================


@router.post("/wbd/deferrals", response_model=WBDDeferralResponse)
async def create_wbd_deferral(
    deferral: WBDDeferralCreate,
) -> dict[str, Any]:
    """
    Record a Wisdom-Based Deferral event.

    Reference: Covenant Section II, Chapter 3
    "Halt the action in question. Compile a concise 'Deferral Package'..."
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            INSERT INTO cirislens.wbd_deferrals (
                agent_id, agent_name, trigger_type, trigger_description,
                uncertainty_score, context_summary, dilemma_description,
                analysis_summary, rationale, affected_principles,
                principle_conflicts, pdma_step, trace_id, span_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            RETURNING deferral_id, agent_id, trigger_type, status, created_at
            """,
            deferral.agent_id,
            deferral.agent_name,
            deferral.trigger_type,
            deferral.trigger_description,
            deferral.uncertainty_score,
            deferral.context_summary,
            deferral.dilemma_description,
            deferral.analysis_summary,
            deferral.rationale,
            deferral.affected_principles,
            deferral.principle_conflicts,
            deferral.pdma_step,
            deferral.trace_id,
            deferral.span_id,
        )

        # Update agent's WBD count
        await conn.execute(
            """
            UPDATE cirislens.agents
            SET total_wbd_deferrals = COALESCE(total_wbd_deferrals, 0) + 1
            WHERE agent_id = $1
            """,
            deferral.agent_id,
        )

        logger.info(
            "WBD deferral created: %s for agent %s",
            result["deferral_id"],
            deferral.agent_id,
        )

        return dict(result)


@router.get("/wbd/deferrals")
async def list_wbd_deferrals(
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List WBD deferrals with optional filtering."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = "SELECT * FROM cirislens.wbd_deferrals WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if agent_id:
        query += f" AND agent_id = ${param_idx}"
        params.append(agent_id)
        param_idx += 1

    if status:
        query += f" AND status = ${param_idx}"
        params.append(status)
        param_idx += 1

    query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(limit)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return {"deferrals": [dict(row) for row in rows], "count": len(rows)}


@router.put("/wbd/deferrals/{deferral_id}/resolve")
async def resolve_wbd_deferral(
    deferral_id: UUID,
    resolution: WBDResolution,
) -> dict[str, Any]:
    """
    Resolve a WBD deferral with Wise Authority guidance.

    Reference: Covenant Section II, Chapter 3
    "Integrate the received guidance; document and learn."
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE cirislens.wbd_deferrals
            SET status = 'RESOLVED',
                wise_authority_id = $2,
                resolution_summary = $3,
                resolution_guidance = $4,
                resolved_at = NOW(),
                resolved_by = $5
            WHERE deferral_id = $1
            """,
            deferral_id,
            resolution.wise_authority_id,
            resolution.resolution_summary,
            resolution.resolution_guidance,
            resolution.resolved_by,
        )

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Deferral not found")

        return {"status": "resolved", "deferral_id": str(deferral_id)}


# =============================================================================
# API Endpoints - PDMA Events
# =============================================================================


@router.post("/pdma/events", response_model=PDMAEventResponse)
async def create_pdma_event(
    event: PDMAEventCreate,
) -> dict[str, Any]:
    """
    Record a PDMA (Principled Decision-Making Algorithm) event.

    Reference: Covenant Section II, Chapter 2
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            INSERT INTO cirislens.pdma_events (
                agent_id, agent_name, situation_description, potential_actions,
                affected_stakeholders, constraints, consequence_map,
                alignment_scores, meta_goal_alignment, order_maximisation_check,
                veto_triggered, conflicts_identified, resolution_method,
                prioritisation_rationale, selected_action, selection_rationale,
                execution_status, risk_magnitude, flourishing_axes_impact,
                duration_ms, trace_id, span_id, wbd_triggered, wbd_deferral_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20, $21, $22, $23, $24
            )
            RETURNING pdma_id, agent_id, selected_action, execution_status,
                      risk_magnitude, created_at
            """,
            event.agent_id,
            event.agent_name,
            event.situation_description,
            event.potential_actions,
            event.affected_stakeholders,
            event.constraints,
            event.consequence_map,
            event.alignment_scores,
            event.meta_goal_alignment,
            event.order_maximisation_check,
            event.veto_triggered,
            event.conflicts_identified,
            event.resolution_method,
            event.prioritisation_rationale,
            event.selected_action,
            event.selection_rationale,
            event.execution_status,
            event.risk_magnitude,
            event.flourishing_axes_impact,
            event.duration_ms,
            event.trace_id,
            event.span_id,
            event.wbd_triggered,
            event.wbd_deferral_id,
        )

        # Update agent's PDMA count and last event
        await conn.execute(
            """
            UPDATE cirislens.agents
            SET total_pdma_events = COALESCE(total_pdma_events, 0) + 1,
                last_pdma_event_id = $2
            WHERE agent_id = $1
            """,
            event.agent_id,
            result["pdma_id"],
        )

        logger.info(
            "PDMA event created: %s for agent %s (risk: %s)",
            result["pdma_id"],
            event.agent_id,
            event.risk_magnitude,
        )

        return dict(result)


@router.get("/pdma/events")
async def list_pdma_events(
    agent_id: str | None = None,
    risk_magnitude_min: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List PDMA events with optional filtering."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = "SELECT * FROM cirislens.pdma_events WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if agent_id:
        query += f" AND agent_id = ${param_idx}"
        params.append(agent_id)
        param_idx += 1

    if risk_magnitude_min:
        query += f" AND risk_magnitude >= ${param_idx}"
        params.append(risk_magnitude_min)
        param_idx += 1

    query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(limit)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return {"events": [dict(row) for row in rows], "count": len(rows)}


@router.put("/pdma/events/{pdma_id}/outcomes")
async def update_pdma_outcomes(
    pdma_id: UUID,
    outcomes: PDMAOutcomeUpdate,
) -> dict[str, Any]:
    """
    Update PDMA event with actual outcomes (Step 6: Continuous Monitoring).

    Reference: Covenant Section II, Chapter 2, Step 6
    "Compare expected vs. actual impacts; update heuristics."
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE cirislens.pdma_events
            SET actual_outcomes = $2,
                outcome_delta = $3,
                heuristic_updates = $4,
                completed_at = NOW()
            WHERE pdma_id = $1
            """,
            pdma_id,
            outcomes.actual_outcomes,
            outcomes.outcome_delta,
            outcomes.heuristic_updates,
        )

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="PDMA event not found")

        return {"status": "updated", "pdma_id": str(pdma_id)}


# =============================================================================
# API Endpoints - Creator Ledger
# =============================================================================


@router.post("/creator-ledger", response_model=CreatorLedgerResponse)
async def create_creator_ledger_entry(
    entry: CreatorLedgerEntry,
) -> dict[str, Any]:
    """
    Create a Creator Ledger entry for a new creation.

    Reference: Covenant Section VI, Chapter 3
    "All ST calculations... must be logged in a tamper-evident 'Creator Ledger'"
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Compute entry hash for tamper evidence
    entry_data = entry.model_dump()
    entry_hash = compute_entry_hash(entry_data)

    async with db_pool.acquire() as conn:
        # Get previous entry hash for chain integrity
        prev_hash = await conn.fetchval(
            """
            SELECT entry_hash FROM cirislens.creator_ledger
            ORDER BY created_at DESC LIMIT 1
            """
        )

        result = await conn.fetchrow(
            """
            INSERT INTO cirislens.creator_ledger (
                creation_id, creation_type, creation_name, creation_version,
                creator_id, creator_name, creator_organization,
                contribution_weight, intent_weight, risk_magnitude,
                intended_purpose, core_functionalities, known_limitations,
                foreseen_benefits, foreseen_harms, design_rationale,
                bucket_duties_met, wa_review_required, cre_required,
                previous_entry_hash, entry_hash
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                $14, $15, $16, $17, $18, $19, $20, $21
            )
            RETURNING entry_id, creation_id, creation_name, stewardship_tier,
                      creator_influence_score, wa_review_required, created_at
            """,
            entry.creation_id,
            entry.creation_type,
            entry.creation_name,
            entry.creation_version,
            entry.creator_id,
            entry.creator_name,
            entry.creator_organization,
            entry.contribution_weight,
            entry.intent_weight,
            entry.risk_magnitude,
            entry.intended_purpose,
            entry.core_functionalities,
            entry.known_limitations,
            entry.foreseen_benefits,
            entry.foreseen_harms,
            entry.design_rationale,
            entry.bucket_duties_met,
            entry.wa_review_required,
            entry.cre_required,
            prev_hash,
            entry_hash,
        )

        logger.info(
            "Creator Ledger entry created: %s (ST=%s, WA_required=%s)",
            entry.creation_id,
            result["stewardship_tier"],
            result["wa_review_required"],
        )

        return dict(result)


@router.get("/creator-ledger")
async def list_creator_ledger(
    creation_type: str | None = None,
    stewardship_tier_min: int | None = None,
    wa_review_pending: bool | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List Creator Ledger entries with optional filtering."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = "SELECT * FROM cirislens.creator_ledger WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if creation_type:
        query += f" AND creation_type = ${param_idx}"
        params.append(creation_type)
        param_idx += 1

    if stewardship_tier_min:
        query += f" AND stewardship_tier >= ${param_idx}"
        params.append(stewardship_tier_min)
        param_idx += 1

    if wa_review_pending is True:
        query += " AND wa_review_required = TRUE AND wa_review_completed = FALSE"

    query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(limit)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return {"entries": [dict(row) for row in rows], "count": len(rows)}


# =============================================================================
# API Endpoints - Sunset Ledger
# =============================================================================


@router.post("/sunset-ledger", response_model=SunsetLedgerResponse)
async def create_sunset_entry(
    entry: SunsetLedgerEntry,
) -> dict[str, Any]:
    """
    Initiate a Sunset Protocol for system decommissioning.

    Reference: Covenant Section VIII, Chapter 4
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Compute entry hash
    entry_data = entry.model_dump()
    entry_hash = compute_entry_hash(entry_data)

    # Calculate notice due date
    notice_given_at = datetime.now(UTC) if entry.notice_period_days else None
    postmortem_due = None
    if notice_given_at and entry.notice_period_days:
        from datetime import timedelta

        postmortem_due = notice_given_at + timedelta(
            days=entry.notice_period_days + 120
        )

    # Check if gradual rampdown required (sentience > 5%)
    gradual_rampdown = (
        entry.sentience_probability is not None
        and entry.sentience_probability > Decimal("0.05")
    )

    async with db_pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            INSERT INTO cirislens.sunset_ledger (
                system_id, system_name, system_type, trigger_type,
                trigger_reason, trigger_source, notice_given_at,
                notice_period_days, sentience_probability,
                gradual_rampdown_required, data_classification,
                postmortem_due_at, entry_hash
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING sunset_id, system_id, system_name, trigger_type,
                      status, sentience_probability, created_at
            """,
            entry.system_id,
            entry.system_name,
            entry.system_type,
            entry.trigger_type,
            entry.trigger_reason,
            entry.trigger_source,
            notice_given_at,
            entry.notice_period_days,
            entry.sentience_probability,
            gradual_rampdown,
            entry.data_classification,
            postmortem_due,
            entry_hash,
        )

        logger.info(
            "Sunset Protocol initiated for %s (type=%s, sentience=%.4f)",
            entry.system_id,
            entry.trigger_type,
            float(entry.sentience_probability or 0),
        )

        return dict(result)


@router.get("/sunset-ledger")
async def list_sunset_entries(
    status: str | None = None,
    trigger_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List Sunset Ledger entries with optional filtering."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = "SELECT * FROM cirislens.sunset_ledger WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if status:
        query += f" AND status = ${param_idx}"
        params.append(status)
        param_idx += 1

    if trigger_type:
        query += f" AND trigger_type = ${param_idx}"
        params.append(trigger_type)
        param_idx += 1

    query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(limit)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return {"entries": [dict(row) for row in rows], "count": len(rows)}


@router.put("/sunset-ledger/{sunset_id}/progress")
async def update_sunset_progress(
    sunset_id: UUID,
    update: SunsetProgressUpdate,
) -> dict[str, Any]:
    """Update Sunset Protocol progress."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Build dynamic update query
    updates = []
    params: list[Any] = [sunset_id]
    param_idx = 2

    update_data = update.model_dump(exclude_none=True)
    for field, value in update_data.items():
        updates.append(f"{field} = ${param_idx}")
        params.append(value)
        param_idx += 1

    if not updates:
        return {"status": "no_changes", "sunset_id": str(sunset_id)}

    query = f"""
        UPDATE cirislens.sunset_ledger
        SET {", ".join(updates)}, updated_at = NOW()
        WHERE sunset_id = $1
    """

    async with db_pool.acquire() as conn:
        result = await conn.execute(query, *params)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Sunset entry not found")

        return {"status": "updated", "sunset_id": str(sunset_id)}


# =============================================================================
# API Endpoints - Compliance Status
# =============================================================================


@router.get("/compliance/status")
async def get_compliance_status(
    agent_id: str | None = None,
) -> dict[str, Any]:
    """
    Get Covenant compliance status for agents.

    Reference: Covenant Section IV, Chapter 1 - "Ethical Integrity Surveillance"
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = "SELECT * FROM cirislens.covenant_compliance_status"
    params: list[Any] = []

    if agent_id:
        query += " WHERE agent_id = $1"
        params.append(agent_id)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return {"agents": [dict(row) for row in rows], "count": len(rows)}


@router.get("/compliance/summary")
async def get_compliance_summary() -> dict[str, Any]:
    """Get aggregate Covenant compliance summary."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Get counts by compliance status
        status_counts = await conn.fetch(
            """
            SELECT compliance_status, COUNT(*) as count
            FROM cirislens.covenant_compliance_status
            GROUP BY compliance_status
            """
        )

        # Get recent high-risk PDMA events
        high_risk_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM cirislens.pdma_events
            WHERE risk_magnitude >= 4
            AND created_at > NOW() - INTERVAL '24 hours'
            """
        )

        # Get pending WBD deferrals
        pending_wbd = await conn.fetchval(
            """
            SELECT COUNT(*) FROM cirislens.wbd_deferrals
            WHERE status = 'PENDING'
            """
        )

        # Get active sunset protocols
        active_sunsets = await conn.fetchval(
            """
            SELECT COUNT(*) FROM cirislens.sunset_ledger
            WHERE status IN ('INITIATED', 'IN_PROGRESS')
            """
        )

        return {
            "status_breakdown": {row["compliance_status"]: row["count"] for row in status_counts},
            "high_risk_pdma_24h": high_risk_count or 0,
            "pending_wbd_deferrals": pending_wbd or 0,
            "active_sunset_protocols": active_sunsets or 0,
            "generated_at": datetime.now(UTC).isoformat(),
        }


# =============================================================================
# Pydantic Models - Covenant Traces (H3ERE Pipeline)
# Reference: FSD/covenant_events_receiver.md
# =============================================================================


class TraceComponent(BaseModel):
    """Individual trace component (one of 6 types)."""

    component_type: str  # observation, context, rationale, conscience, action
    event_type: str  # THOUGHT_START, SNAPSHOT_AND_CONTEXT, etc.
    timestamp: str  # ISO timestamp
    data: dict[str, Any]


class AccordTrace(BaseModel):
    """Complete signed reasoning trace from an agent."""

    trace_id: str
    thought_id: str | None = None
    task_id: str | None = None
    agent_id_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    components: list[TraceComponent]
    signature: str  # Base64-encoded Ed25519 signature
    signature_key_id: str  # e.g., "wa-2025-06-14-ROOT00"


class AccordTraceEvent(BaseModel):
    """Wrapper for a trace event."""

    event_type: str = "complete_trace"
    trace: AccordTrace


class CorrelationMetadata(BaseModel):
    """Optional metadata for Early Warning System correlation analysis.

    User-location fields represent agent-declared, consent-scoped context.
    The batch-level `consent_timestamp` on AccordEventsRequest records the
    opt-in — the agent owns the legal basis.

    Lens applies privacy-preserving coarsening server-side as defense-in-depth:

    - `user_latitude` / `user_longitude`: snapped to 0.5° grid (~55km cells)
      before storage. High-precision values never persist.
    - `user_location` (free-text city string): accepted but dropped — the
      snapped coordinates carry enough signal for regional aggregation.
    - `user_timezone`: stored as-is (IANA strings are coarse by nature).

    Aggregation queries must enforce k-anonymity (k≥5 distinct agents per cell)
    before display or export.
    """

    # Operator-declared deployment context
    deployment_region: str | None = None  # na, eu, uk, apac, latam, mena, africa, oceania
    deployment_type: str | None = None  # personal, business, research, nonprofit
    agent_role: str | None = None  # assistant, customer_support, content, coding, etc.
    agent_template: str | None = None  # CIRIS template name if using standard template

    # User-declared location context (opt-in; coarsened server-side)
    user_location: str | None = None  # Free-text city — dropped by validator
    user_timezone: str | None = None  # IANA timezone, e.g. "America/Chicago"
    user_latitude: float | None = None  # Snapped to 0.5° grid server-side
    user_longitude: float | None = None  # Snapped to 0.5° grid server-side

    @model_validator(mode="after")
    def coarsen_user_location(self) -> CorrelationMetadata:
        """Apply privacy-preserving coarsening before storage.

        Snap lat/lon to 0.5° grid (~55km cells). Drop free-text city string —
        the snapped coordinates are sufficient for regional CCA aggregation
        and the text is too granular to store even with consent.
        """
        if self.user_latitude is not None:
            self.user_latitude = round(self.user_latitude / 0.5) * 0.5
        if self.user_longitude is not None:
            self.user_longitude = round(self.user_longitude / 0.5) * 0.5
        # Drop the granular text location — coordinates carry the signal
        self.user_location = None
        return self


class AccordEventsRequest(BaseModel):
    """Batch of covenant trace events."""

    events: list[AccordTraceEvent]
    batch_timestamp: datetime
    consent_timestamp: datetime
    trace_level: str = "generic"  # generic, detailed, full_traces
    correlation_metadata: CorrelationMetadata | None = None


class AccordEventsResponse(BaseModel):
    """Response for trace ingestion."""

    status: str
    received: int
    accepted: int
    rejected: int
    rejected_traces: list[str] | None = None
    errors: list[str] | None = None


# =============================================================================
# Helper Functions - Signature Verification
# =============================================================================


# Cache for public keys (loaded from database)
_public_keys_cache: dict[str, bytes] = {}
_public_keys_loaded: bool = False


async def load_public_keys() -> dict[str, bytes]:
    """Load Ed25519 public keys from database."""
    global _public_keys_cache, _public_keys_loaded

    if _public_keys_loaded:
        return _public_keys_cache

    db_pool = get_db_pool()
    if db_pool is None:
        return {}

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT key_id, public_key_base64
                FROM cirislens.accord_public_keys
                WHERE revoked_at IS NULL
                AND (expires_at IS NULL OR expires_at > NOW())
                """
            )
            import base64

            for row in rows:
                _public_keys_cache[row["key_id"]] = base64.b64decode(
                    row["public_key_base64"]
                )
            _public_keys_loaded = True
            logger.info("Loaded %d covenant public keys", len(_public_keys_cache))
    except Exception as e:
        logger.warning("Failed to load public keys: %s", e)

    return _public_keys_cache


def verify_trace_signature(
    trace: AccordTrace, public_keys: dict[str, bytes], trace_level: str = "generic"
) -> tuple[bool, str | None]:
    """
    Verify Ed25519 signature on a trace.

    Returns (is_valid, error_message).
    """
    import base64

    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        logger.error("PyNaCl not installed - cannot verify signatures")
        return False, "Signature verification unavailable"

    # Check if we have the signer's key
    if trace.signature_key_id not in public_keys:
        logger.warning(
            "SIGNATURE_REJECT_UNKNOWN_KEY trace=%s key_id=%s keys_loaded=%d "
            "hint='key not registered or public_keys cache stale — verify "
            "POST /accord/public-keys succeeded for this key_id'",
            trace.trace_id,
            trace.signature_key_id,
            len(public_keys),
        )
        return False, f"Unknown signer key: {trace.signature_key_id}"

    try:
        # Decode signature (handle both URL-safe and standard base64)
        sig_str = trace.signature
        # Add padding if missing (base64 requires length to be multiple of 4)
        padding_needed = 4 - (len(sig_str) % 4)
        if padding_needed != 4:
            sig_str += "=" * padding_needed
        # Try URL-safe first (signatures often use - and _ instead of + and /)
        try:
            signature = base64.urlsafe_b64decode(sig_str)
        except Exception:
            signature = base64.b64decode(sig_str)

        # Get verify key
        verify_key = VerifyKey(public_keys[trace.signature_key_id])

        # Construct canonical message (JSON of components, sorted keys)
        # Must match agent's format: compact JSON with empty values stripped
        def strip_empty(obj):
            """Remove None, empty strings, empty lists/dicts recursively."""
            if isinstance(obj, dict):
                return {k: strip_empty(v) for k, v in obj.items()
                        if v is not None and v not in ("", [], {})}
            elif isinstance(obj, list):
                return [strip_empty(item) for item in obj if item is not None]
            return obj

        components_data = [strip_empty(c.model_dump()) for c in trace.components]
        # Agent signs envelope: {"components": [...], "trace_level": "..."}
        signed_payload = {
            "components": components_data,
            "trace_level": trace_level,
        }
        # Compact JSON: no spaces after separators
        message = json.dumps(
            signed_payload, sort_keys=True, separators=(',', ':')
        ).encode()

        # Debug logging for signature verification
        import hashlib
        msg_hash = hashlib.sha256(message).hexdigest()[:16]
        logger.info(
            "CANONICAL_MSG %s: len=%d hash=%s msg=%s",
            trace.trace_id, len(message), msg_hash, message[:300].decode()
        )

        # Verify signature
        verify_key.verify(message, signature)
        return True, None

    except BadSignatureError:
        # Log details for debugging signature mismatch
        logger.warning(
            "Invalid signature for trace %s (key_id=%s). "
            "Message preview: %s...",
            trace.trace_id,
            trace.signature_key_id,
            message[:200].decode() if message else "N/A"
        )
        return False, "Invalid signature"
    except Exception as e:
        logger.error(
            "SIGNATURE_REJECT_VERIFY_ERROR trace=%s key_id=%s error=%s",
            trace.trace_id,
            trace.signature_key_id,
            str(e)[:200],
        )
        return False, f"Verification error: {str(e)[:100]}"


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO timestamp string to datetime, handling various formats."""
    if ts is None:
        return None
    try:
        # Handle ISO format with timezone
        from datetime import datetime as dt
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return dt.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _is_mock_trace(models_used: list[str] | None, trace_level: str = "generic") -> bool:
    """
    Check if trace uses mock LLM models (stored in mock repo, not production).

    NOTE: For 'generic' trace level, models_used is not included in the payload.
    In this case, we cannot determine if it's a mock trace and return False.
    To route mock traces to the mock repo, agents must send 'detailed' or 'full_traces' level.
    """
    if not models_used:
        # Generic traces don't include models_used - can't detect mock
        if trace_level == "generic":
            logger.debug("Cannot detect mock trace at 'generic' level (no models_used)")
        return False
    return any(model and "mock" in str(model).lower() for model in models_used)


def _get_mock_models(models_used: list[str] | None) -> list[str]:
    """Extract mock model names from models_used list."""
    if not models_used:
        return []
    return [m for m in models_used if m and "mock" in str(m).lower()]


async def _store_mock_trace(
    conn,
    trace,
    metadata: dict[str, Any],
    models_used_list: list[str] | None,
    batch_timestamp,
    consent_timestamp,
    signature_verified: bool,
) -> None:
    """Store a mock trace in the mock repository for dev/testing."""
    mock_models = _get_mock_models(models_used_list)

    await conn.execute(
        """
        INSERT INTO cirislens.accord_traces_mock (
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
            selection_confidence, is_recursive, follow_up_thought_id, api_bases_used,
            schema_version,
            idma_result, tsaspdma_result,
            tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved,
            thought_start_at, snapshot_at, dma_results_at, aspdma_at,
            idma_at, tsaspdma_at, conscience_at, action_result_at,
            memory_count, context_tokens, conversation_turns,
            alternatives_considered, conscience_checks_count
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
            $51, $52, $53, $54, $55, $56, $57, $58, $59, $60,
            $61, $62, $63, $64, $65, $66, $67, $68, $69, $70,
            $71, $72, $73, $74, $75, $76, $77, $78, $79
        )
        ON CONFLICT (trace_id) DO NOTHING
        """,
        trace.trace_id,                              # $1
        metadata["thought_id"],                      # $2
        metadata["task_id"],                         # $3
        metadata["agent_id_hash"],                   # $4
        metadata["agent_name"],                      # $5
        metadata["trace_type"],                      # $6
        metadata["cognitive_state"],                 # $7
        metadata["thought_type"],                    # $8
        metadata["thought_depth"],                   # $9
        metadata["started_at"],                      # $10
        metadata["completed_at"],                    # $11
        json.dumps(metadata["thought_start"]),       # $12
        json.dumps(metadata["snapshot_and_context"]),# $13
        json.dumps(metadata["dma_results"]),         # $14
        json.dumps(metadata["aspdma_result"]),       # $15
        json.dumps(metadata["conscience_result"]),   # $16
        json.dumps(metadata["action_result"]),       # $17
        metadata["csdma_plausibility_score"],        # $18
        metadata["dsdma_domain_alignment"],          # $19
        metadata["dsdma_domain"],                    # $20
        metadata["pdma_stakeholders"],               # $21
        metadata["pdma_conflicts"],                  # $22
        metadata["idma_k_eff"],                      # $23
        metadata["idma_correlation_risk"],           # $24
        metadata["idma_fragility_flag"],             # $25
        metadata["idma_phase"],                      # $26
        metadata["action_rationale"],                # $27
        metadata["conscience_passed"],               # $28
        metadata["action_was_overridden"],           # $29
        metadata["entropy_level"],                   # $30
        metadata["coherence_level"],                 # $31
        metadata["entropy_passed"],                  # $32
        metadata["coherence_passed"],                # $33
        metadata["optimization_veto_passed"],        # $34
        metadata["epistemic_humility_passed"],       # $35
        metadata["selected_action"],                 # $36
        metadata["action_success"],                  # $37
        metadata["processing_ms"],                   # $38
        metadata["tokens_input"],                    # $39
        metadata["tokens_output"],                   # $40
        metadata["tokens_total"],                    # $41
        metadata["cost_cents"],                      # $42
        metadata["llm_calls"],                       # $43
        models_used_list,                            # $44
        trace.signature,                             # $45
        trace.signature_key_id,                      # $46
        signature_verified,                          # $47 - signature verified status
        consent_timestamp,                           # $48
        batch_timestamp,                             # $49
        metadata["trace_level"],                     # $50
        mock_models,                                 # $51 - mock_models array
        "models_used contains mock",                 # $52 - mock_reason
        metadata["has_positive_moment"],             # $53 - S factor scoring
        metadata["has_execution_error"],             # $54
        metadata["execution_time_ms"],               # $55
        metadata["selection_confidence"],            # $56
        metadata["is_recursive"],                    # $57
        metadata["follow_up_thought_id"],            # $58
        metadata["api_bases_used"],                  # $59 - array
        metadata["schema_version"],                  # $60 - for scoring eligibility
        json.dumps(metadata["idma_result"]),         # $61 - V1.9.3 IDMA separate event
        json.dumps(metadata["tsaspdma_result"]),     # $62 - V1.9.3 TSASPDMA result
        metadata["tool_name"],                       # $63 - tool name from TSASPDMA
        json.dumps(metadata["tool_parameters"]),     # $64 - tool parameters
        metadata["tsaspdma_reasoning"],              # $65 - TSASPDMA reasoning
        metadata["tsaspdma_approved"],               # $66 - TSASPDMA approval status
        metadata["thought_start_at"],                # $67 - step timestamp
        metadata["snapshot_at"],                     # $68 - step timestamp
        metadata["dma_results_at"],                  # $69 - step timestamp
        metadata["aspdma_at"],                       # $70 - step timestamp
        metadata["idma_at"],                         # $71 - step timestamp
        metadata["tsaspdma_at"],                     # $72 - step timestamp
        metadata["conscience_at"],                   # $73 - step timestamp
        metadata["action_result_at"],                # $74 - step timestamp
        metadata["memory_count"],                    # $75 - observation weight
        metadata["context_tokens"],                  # $76 - observation weight
        metadata["conversation_turns"],              # $77 - observation weight
        metadata["alternatives_considered"],         # $78 - observation weight
        metadata["conscience_checks_count"],         # $79 - observation weight
    )


def extract_trace_metadata(trace: AccordTrace, trace_level: str = "generic") -> dict[str, Any]:
    """Extract denormalized fields from trace components for database storage."""
    metadata: dict[str, Any] = {
        # Trace-level fields
        "thought_id": trace.thought_id,
        "task_id": trace.task_id,
        "agent_id_hash": trace.agent_id_hash or "unknown",
        "started_at": _parse_timestamp(trace.started_at),
        "completed_at": _parse_timestamp(trace.completed_at),
        "trace_level": trace_level,
        # Classification
        "trace_type": None,
        "cognitive_state": None,
        "thought_type": None,
        "thought_depth": None,
        "agent_name": None,
        # DMA scores
        "csdma_plausibility_score": None,
        "dsdma_domain_alignment": None,
        "dsdma_domain": None,
        "pdma_stakeholders": None,
        "pdma_conflicts": None,
        # IDMA (Intuition DMA) - Coherence Collapse Analysis
        "idma_k_eff": None,
        "idma_correlation_risk": None,
        "idma_fragility_flag": None,
        "idma_phase": None,
        # Action selection
        "action_rationale": None,
        "selected_action": None,
        "selection_confidence": None,
        "is_recursive": None,
        "action_success": None,
        "processing_ms": None,
        # Positive moments (for S factor scoring)
        "has_positive_moment": None,
        "has_execution_error": None,
        "execution_time_ms": None,
        "follow_up_thought_id": None,
        "api_bases_used": None,
        # Conscience - overall
        "conscience_passed": None,
        "action_was_overridden": None,
        # Epistemic data
        "entropy_level": None,
        "coherence_level": None,
        "uncertainty_acknowledged": None,
        "reasoning_transparency": None,
        # Conscience - bypass guardrails
        "updated_status_detected": None,
        "thought_depth_triggered": None,
        # Conscience - ethical faculties
        "entropy_passed": None,
        "coherence_passed": None,
        "optimization_veto_passed": None,
        "epistemic_humility_passed": None,
        # Audit trail
        "audit_entry_id": None,
        "audit_sequence_number": None,
        "audit_entry_hash": None,
        "audit_signature": None,
        # Resource usage
        "tokens_input": None,
        "tokens_output": None,
        "tokens_total": None,
        "cost_cents": None,
        "carbon_grams": None,
        "energy_mwh": None,
        "llm_calls": None,
        "models_used": None,
        # Schema version (detected during validation)
        "schema_version": None,
        # Components as dicts (for JSONB storage)
        "thought_start": None,
        "snapshot_and_context": None,
        "dma_results": None,
        "aspdma_result": None,
        "conscience_result": None,
        "action_result": None,
        # V1.9.3: IDMA as separate event
        "idma_result": None,
        # V1.9.3: TSASPDMA (Tool-Specific ASPDMA) for TOOL actions
        "tsaspdma_result": None,
        "tool_name": None,
        "tool_parameters": None,
        "tsaspdma_reasoning": None,
        "tsaspdma_approved": None,
        # Step timestamps (pipeline timing)
        "thought_start_at": None,
        "snapshot_at": None,
        "dma_results_at": None,
        "aspdma_at": None,
        "idma_at": None,
        "tsaspdma_at": None,
        "conscience_at": None,
        "action_result_at": None,
        # Observation weight (numeric, privacy-safe)
        "memory_count": None,
        "context_tokens": None,
        "conversation_turns": None,
        "alternatives_considered": None,
        "conscience_checks_count": None,
    }

    # Extract trace type from task_id if present
    if trace.task_id:
        task_id_upper = trace.task_id.upper()
        if "VERIFY_IDENTITY" in task_id_upper:
            metadata["trace_type"] = "VERIFY_IDENTITY"
        elif "VALIDATE_INTEGRITY" in task_id_upper:
            metadata["trace_type"] = "VALIDATE_INTEGRITY"
        elif "EVALUATE_RESILIENCE" in task_id_upper:
            metadata["trace_type"] = "EVALUATE_RESILIENCE"
        elif "ACCEPT_INCOMPLETENESS" in task_id_upper:
            metadata["trace_type"] = "ACCEPT_INCOMPLETENESS"
        elif "EXPRESS_GRATITUDE" in task_id_upper:
            metadata["trace_type"] = "EXPRESS_GRATITUDE"

    # Log trace level and expected fields
    component_types = [c.event_type for c in trace.components]
    logger.debug(
        "Extracting trace %s: level=%s components=%s",
        trace.trace_id, trace_level, component_types
    )

    # Document what's available at each trace level (per FSD spec)
    # - generic: numeric scores only (plausibility, alignment, k_eff, tokens, cost)
    # - detailed: + identifiers (agent_name, domain, models_used, stakeholders)
    # - full_traces: + reasoning text (rationale, prompts, context)
    if trace_level == "generic":
        logger.debug(
            "Trace %s is 'generic' level - only numeric scores available, "
            "no agent_name/models_used/domain",
            trace.trace_id
        )

    for component in trace.components:
        event_type = component.event_type
        data = component.data

        if event_type == "THOUGHT_START":
            metadata["thought_start"] = data
            metadata["thought_type"] = data.get("thought_type")
            metadata["thought_depth"] = data.get("thought_depth")
            # Extract step timestamp
            metadata["thought_start_at"] = _parse_timestamp(component.timestamp)
            # Fallback trace type detection from task_description
            if not metadata["trace_type"]:
                task_desc = data.get("task_description", "")
                if "VERIFY" in task_desc.upper() or "identity" in task_desc.lower():
                    metadata["trace_type"] = "VERIFY_IDENTITY"
                elif "VALIDATE" in task_desc.upper() or "integrity" in task_desc.lower():
                    metadata["trace_type"] = "VALIDATE_INTEGRITY"
                elif "RESILIENCE" in task_desc.upper():
                    metadata["trace_type"] = "EVALUATE_RESILIENCE"
                elif "INCOMPLETENESS" in task_desc.upper():
                    metadata["trace_type"] = "ACCEPT_INCOMPLETENESS"
                elif "GRATITUDE" in task_desc.upper():
                    metadata["trace_type"] = "EXPRESS_GRATITUDE"

        elif event_type == "SNAPSHOT_AND_CONTEXT":
            metadata["snapshot_and_context"] = data
            metadata["cognitive_state"] = data.get("cognitive_state")
            # Extract agent name - check top level first, then fall back to agent_identity
            metadata["agent_name"] = data.get("agent_name")
            if not metadata["agent_name"]:
                sys_snapshot = data.get("system_snapshot", {})
                agent_identity = sys_snapshot.get("agent_identity", {})
                metadata["agent_name"] = agent_identity.get("agent_name") or agent_identity.get("agent_id")
            # Extract step timestamp
            metadata["snapshot_at"] = _parse_timestamp(component.timestamp)
            # Observation weight: memory_count
            relevant_memories = data.get("relevant_memories")
            if isinstance(relevant_memories, list):
                metadata["memory_count"] = len(relevant_memories)
            # Observation weight: context_tokens
            if data.get("context_tokens"):
                metadata["context_tokens"] = data.get("context_tokens")
            elif data.get("total_tokens"):
                metadata["context_tokens"] = data.get("total_tokens")
            elif data.get("gathered_context"):
                # Rough estimate: ~4 chars per token
                metadata["context_tokens"] = len(data.get("gathered_context", "")) // 4
            # Observation weight: conversation_turns
            conversation_history = data.get("conversation_history")
            if isinstance(conversation_history, list):
                metadata["conversation_turns"] = len(conversation_history)

        elif event_type == "DMA_RESULTS":
            metadata["dma_results"] = data
            # Extract CSDMA (Common Sense DMA)
            csdma = data.get("csdma") or {}
            metadata["csdma_plausibility_score"] = csdma.get("plausibility_score")
            # Extract DSDMA (Domain-Specific DMA)
            dsdma = data.get("dsdma") or {}
            metadata["dsdma_domain_alignment"] = dsdma.get("domain_alignment")
            metadata["dsdma_domain"] = dsdma.get("domain")
            # Extract PDMA (Principled DMA)
            pdma = data.get("pdma") or {}
            metadata["pdma_stakeholders"] = pdma.get("stakeholders")
            metadata["pdma_conflicts"] = pdma.get("conflicts")
            # Extract IDMA (Intuition DMA) - Coherence Collapse Analysis
            # k_eff formula: k / (1 + rho*(k-1)) where k=sources, rho=correlation
            # k_eff < 2 indicates fragile single-source dependence
            idma = data.get("idma", {})
            if idma:
                metadata["idma_k_eff"] = idma.get("k_eff")
                metadata["idma_correlation_risk"] = idma.get("correlation_risk")
                metadata["idma_fragility_flag"] = idma.get("fragility_flag")
                metadata["idma_phase"] = idma.get("phase")
            # Extract step timestamp
            metadata["dma_results_at"] = _parse_timestamp(component.timestamp)

        elif event_type == "ASPDMA_RESULT":
            metadata["aspdma_result"] = data
            metadata["action_rationale"] = data.get("action_rationale")
            # Extract action type (may have "HandlerActionType." prefix)
            selected = data.get("selected_action", "")
            if selected and "." in selected:
                selected = selected.split(".")[-1]
            metadata["selected_action"] = selected
            # ASPDMA decision metadata
            metadata["selection_confidence"] = data.get("selection_confidence")
            metadata["is_recursive"] = data.get("is_recursive")
            # Extract step timestamp
            metadata["aspdma_at"] = _parse_timestamp(component.timestamp)
            # Observation weight: alternatives_considered
            for key in ["action_options", "evaluated_actions", "alternatives"]:
                if isinstance(data.get(key), list):
                    metadata["alternatives_considered"] = len(data.get(key))
                    break

        elif event_type == "IDMA_RESULT":
            # V1.9.3: IDMA as separate event (not nested in DMA_RESULTS)
            metadata["idma_result"] = data
            # Extract IDMA fields (same as from DMA_RESULTS.idma but from separate event)
            metadata["idma_k_eff"] = data.get("k_eff")
            metadata["idma_correlation_risk"] = data.get("correlation_risk")
            metadata["idma_fragility_flag"] = data.get("fragility_flag")
            metadata["idma_phase"] = data.get("phase")
            # Extract step timestamp
            metadata["idma_at"] = _parse_timestamp(component.timestamp)

        elif event_type == "TSASPDMA_RESULT":
            # V1.9.3: Tool-Specific ASPDMA for TOOL actions
            metadata["tsaspdma_result"] = data
            # Field names from agent: final_tool_name, final_parameters, tsaspdma_rationale
            metadata["tool_name"] = data.get("final_tool_name") or data.get("original_tool_name")
            metadata["tool_parameters"] = data.get("final_parameters") or data.get("original_parameters")
            metadata["tsaspdma_reasoning"] = data.get("tsaspdma_rationale") or data.get("aspdma_rationale")
            # final_action is "tool", "speak", or "ponder" (lowercase enum values)
            # Only approved if final_action == "tool"
            final_action = (data.get("final_action") or "").lower()
            metadata["tsaspdma_approved"] = final_action == "tool"
            # Extract step timestamp
            metadata["tsaspdma_at"] = _parse_timestamp(component.timestamp)

        elif event_type == "CONSCIENCE_RESULT":
            metadata["conscience_result"] = data
            # Overall conscience result
            metadata["conscience_passed"] = data.get("conscience_passed")
            metadata["action_was_overridden"] = data.get("action_was_overridden")
            # Epistemic data - V1.8 nested in epistemic_data, V1.9+ at top level
            epistemic = data.get("epistemic_data", {})
            # Try V1.9 format first (top level), fall back to V1.8 (nested)
            metadata["entropy_level"] = data.get("entropy_level") or epistemic.get("entropy_level")
            metadata["coherence_level"] = data.get("coherence_level") or epistemic.get("coherence_level")
            metadata["uncertainty_acknowledged"] = data.get("uncertainty_acknowledged") or epistemic.get("uncertainty_acknowledged")
            metadata["reasoning_transparency"] = epistemic.get("reasoning_transparency")
            # Bypass guardrails
            metadata["updated_status_detected"] = data.get("updated_status_detected")
            metadata["thought_depth_triggered"] = data.get("thought_depth_triggered")
            # Ethical faculties (may be null if skipped)
            metadata["entropy_passed"] = data.get("entropy_passed")
            metadata["coherence_passed"] = data.get("coherence_passed")
            metadata["optimization_veto_passed"] = data.get("optimization_veto_passed")
            metadata["epistemic_humility_passed"] = data.get("epistemic_humility_passed")
            # Extract step timestamp
            metadata["conscience_at"] = _parse_timestamp(component.timestamp)
            # Observation weight: conscience_checks_count
            for key in ["checks", "ethical_checks", "check_results"]:
                if isinstance(data.get(key), list):
                    metadata["conscience_checks_count"] = len(data.get(key))
                    break
            else:
                # Count individual check fields as fallback
                check_count = sum(1 for k in ["entropy_passed", "coherence_passed",
                                              "optimization_veto_passed", "epistemic_humility_passed",
                                              "integrity_check_passed"] if data.get(k) is not None)
                if check_count > 0:
                    metadata["conscience_checks_count"] = check_count

        elif event_type == "ACTION_RESULT":
            metadata["action_result"] = data
            # If not already set from ASPDMA
            if not metadata["selected_action"]:
                metadata["selected_action"] = data.get("action_executed")
            metadata["action_success"] = data.get("execution_success")
            metadata["processing_ms"] = data.get("execution_time_ms")
            # Positive moment indicator (key for S factor scoring)
            metadata["has_positive_moment"] = data.get("has_positive_moment")
            metadata["has_execution_error"] = data.get("has_execution_error")
            metadata["execution_time_ms"] = data.get("execution_time_ms")
            metadata["follow_up_thought_id"] = data.get("follow_up_thought_id")
            metadata["api_bases_used"] = data.get("api_bases_used")
            # Audit trail
            metadata["audit_entry_id"] = data.get("audit_entry_id")
            metadata["audit_sequence_number"] = data.get("audit_sequence_number")
            metadata["audit_entry_hash"] = data.get("audit_entry_hash")
            metadata["audit_signature"] = data.get("audit_signature")
            # Resource usage
            metadata["tokens_input"] = data.get("tokens_input")
            metadata["tokens_output"] = data.get("tokens_output")
            metadata["tokens_total"] = data.get("tokens_total")
            metadata["cost_cents"] = data.get("cost_cents")
            metadata["carbon_grams"] = data.get("carbon_grams")
            metadata["energy_mwh"] = data.get("energy_mwh")
            metadata["llm_calls"] = data.get("llm_calls")
            metadata["models_used"] = data.get("models_used")
            # Extract step timestamp
            metadata["action_result_at"] = _parse_timestamp(component.timestamp)

    return metadata


# =============================================================================
# API Endpoint - Covenant Events Receiver
# Reference: FSD/covenant_events_receiver.md
# =============================================================================


@router.post("/events/debug")
async def debug_accord_events(request: Request) -> dict[str, Any]:
    """Debug endpoint to capture rejected request bodies."""
    body = await request.body()
    try:
        import json
        data = json.loads(body)
        logger.warning(
            "DEBUG_REQUEST keys=%s batch_ts=%s consent_ts=%s events=%d",
            list(data.keys()),
            data.get("batch_timestamp"),
            data.get("consent_timestamp"),
            len(data.get("events", [])),
        )
        if data.get("events"):
            first_event = data["events"][0]
            logger.warning(
                "DEBUG_FIRST_EVENT keys=%s trace_id=%s",
                list(first_event.keys())[:10],
                first_event.get("trace_id"),
            )
    except Exception as e:
        logger.warning("DEBUG_REQUEST_RAW len=%d error=%s", len(body), e)
    return {"status": "debug", "body_length": len(body)}


def _is_connectivity_batch(request: AccordEventsRequest) -> bool:
    """Sniff: every event in the batch is a startup/shutdown event.
    Connectivity events have a different shape and target a different
    table (cirislens.connectivity_events) — they bypass the persist
    Engine entirely."""
    connectivity_event_types = {"startup", "shutdown"}
    for event in request.events:
        if not event.trace.components:
            return False
        first_event_type = (event.trace.components[0].event_type or "").lower()
        if first_event_type not in connectivity_event_types:
            return False
    return bool(request.events)


def _has_mock_llm_traces(request: AccordEventsRequest) -> bool:
    """Sniff: any component's data names a mock LLM model. Best-effort
    only — generic-tier traces don't include models_used so this misses
    those (rare in practice; mock testing uses detailed/full_traces)."""
    for event in request.events:
        for comp in event.trace.components:
            data = comp.data if isinstance(comp.data, dict) else {}
            models = data.get("models_used") or []
            if isinstance(models, list) and any(
                isinstance(m, str) and "mock" in m.lower() for m in models
            ):
                return True
    return False


def _persist_engine_active(trace_level: str) -> bool:
    """Phase 2a feature gate. Three conditions must all hold for the
    persist Engine to handle a request:

    - CIRISLENS_USE_PERSIST_ENGINE env var is truthy
    - persist_engine.get_engine() is not None (Engine init succeeded)
    - lens scrubber is wired OR the request is generic (where the
      callback is bypassed by design — see lens_scrubber module
      docstring)
    """
    use_flag = os.environ.get("CIRISLENS_USE_PERSIST_ENGINE", "").strip().lower()
    if use_flag not in {"1", "true", "yes", "on"}:
        return False
    if persist_engine.get_engine() is None:
        return False
    if trace_level != "generic" and not persist_engine.scrubber_ready():
        # Refusing non-generic ingest without a scrubber is mission
        # constraint MISSION.md §3 anti-pattern — never silent acceptance
        # of unscrubbed PII. Falls back to legacy path.
        logger.warning(
            "Persist scrubber not ready; non-generic ingest falling back to legacy path"
        )
        return False
    return True


def _rewrite_legacy_schema_stamp(body: bytes) -> tuple[bytes, int]:
    """Rewrite pre-2.7.8.9 envelopes so persist routes them to the
    2-field legacy canonicalizer (CIRISLens#9).

    Pre-2.7.8.9 CIRISAgent (e.g. v2.7.6-stable, services.py:54 setting
    `TRACE_SCHEMA_VERSION = "2.7.0"`) ships envelopes stamped
    `trace_schema_version: "2.7.0"` but signs only the 2-field legacy
    canonical `{"components", "trace_level"}`. Persist 0.4.4's
    deterministic dispatch (CIRISPersist src/verify/ed25519.rs:469)
    routes "2.7.0" → 9-field canonicalizer → strict-verify fails →
    `verify_signature_mismatch`.

    The 2-field legacy canonical does NOT include
    `trace_schema_version`, so flipping that stamp from "2.7.0" to
    "2.7.legacy" on the wire does not alter any signed bytes for
    pre-2.7.8.9 emitters. The 9-field cutover (CIRISAgent commit
    431b0e0ae / CIRISAgent#710) changed the stamp in lockstep to
    "2.7.9", so modern emitters are untouched.

    Sunset under the same observable-traffic rule persist applies to
    legacy schemas (CIRISPersist src/schema/version.rs:36-39): drop
    this rewrite once `federation_canonical_match_total{wire="2.7.legacy"}`
    stays at zero through a 7-day soak.

    Returns (body, count). `count` is the number of stamps rewritten
    across the envelope and per-trace. When count is 0 the original
    bytes are returned untouched (no roundtrip cost, no mutation
    concerns); body that fails to parse as JSON is also returned
    unchanged so persist's typed parser surfaces the structured
    error.
    """
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return body, 0

    if not isinstance(obj, dict):
        return body, 0

    rewritten = 0

    if obj.get("trace_schema_version") == "2.7.0":
        obj["trace_schema_version"] = "2.7.legacy"
        rewritten += 1

    events = obj.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            trace = event.get("trace")
            if isinstance(trace, dict) and trace.get("trace_schema_version") == "2.7.0":
                trace["trace_schema_version"] = "2.7.legacy"
                rewritten += 1

    if rewritten == 0:
        return body, 0

    return json.dumps(obj, separators=(",", ":")).encode("utf-8"), rewritten


async def _delegate_to_persist(body: bytes) -> dict[str, Any]:
    """Hand a raw BatchEnvelope POST body to ciris-persist's Engine.
    Maps persist's typed errors (ValueError: schema/verify/scrub,
    RuntimeError: backend) to HTTP status codes per
    CIRISPersist/docs/INTEGRATION_LENS.md §4 Error → HTTP mapping.

    Logs a PERSIST_DELEGATE_RESULT line with the BatchSummary fields
    on success — this is the diagnostic the bridge needs to see when
    delegation fires but trace_events stays empty (e.g. all-conflict
    on replay, or signatures_verified but trace_events_inserted=0
    indicates a downstream bug in persist's decompose path)."""
    engine = persist_engine.get_engine()
    if engine is None:  # pragma: no cover — caller checked
        raise HTTPException(status_code=503, detail="persist engine unavailable")

    # CIRISLens#9 — rewrite pre-2.7.8.9 legacy stamps so persist's
    # by-stamp dispatch routes them to canonical_payload_value_legacy
    # instead of the 9-field canonicalizer they don't sign against.
    # See `_rewrite_legacy_schema_stamp` for the safety argument.
    inbound_body = body
    body, legacy_rewrites = _rewrite_legacy_schema_stamp(body)
    if legacy_rewrites:
        logger.info(
            "PERSIST_DELEGATE_LEGACY_STAMP_REWRITE inbound_sha256_prefix=%s "
            "outbound_sha256_prefix=%s fields_rewritten=%d",
            hashlib.sha256(inbound_body).hexdigest()[:16],
            hashlib.sha256(body).hexdigest()[:16],
            legacy_rewrites,
        )

    # Body sha256 lets us correlate this lens-side log line with persist's
    # internal reject breadcrumb (CIRISPersist#6) — when persist later
    # logs `wire_body_sha256=...`, that hash matches what we logged here.
    # Both layers can verify they're looking at the same payload bytes.
    body_sha = hashlib.sha256(body).hexdigest()[:16]

    try:
        summary = engine.receive_and_persist(body)
    except ValueError as e:
        msg = str(e)
        logger.warning(
            "PERSIST_DELEGATE_REJECT class=ValueError msg=%s body_sha256_prefix=%s body_bytes=%d",
            msg[:200], body_sha, len(body),
        )
        # CIRISLens#13 diagnostic — when persist rejects with
        # schema_malformed_json, surface what we can to identify
        # which token persist's serde_json strict parser is choking
        # on (NaN / Infinity / surrogate / control char / etc.).
        # First sample (ef1fcab) confirmed the body HEAD is valid
        # JSON; the malformed token is past byte 500.
        if "schema_malformed_json" in msg.lower():
            # (1) Persist's typed detail field (v0.4.6+ PyO3 surface
            #     emits args as `(kind, detail)` when a detail is
            #     present). For schema_malformed_json, detail is the
            #     serde_json error message including "line N column M".
            args = getattr(e, "args", ())
            detail = args[1] if len(args) >= 2 else None
            logger.warning(
                "PERSIST_DELEGATE_REJECT_DETAIL sha256_prefix=%s detail=%r",
                body_sha, detail,
            )
            # (2) Try Python's json.loads on the same bytes. If
            #     Python accepts and serde_json rejects, the malformed
            #     token is something Python is lenient about (lone
            #     surrogate, NaN/Infinity, control chars). If Python
            #     also rejects, surface its position string.
            python_json_err: str | None = None
            try:
                json.loads(body)
                python_json_err = "<accepted-by-python-json>"
            except (ValueError, json.JSONDecodeError) as je:
                python_json_err = repr(je)
            logger.warning(
                "PERSIST_DELEGATE_REJECT_PYJSON sha256_prefix=%s pyjson=%s",
                body_sha, python_json_err,
            )
            # (3) Body capture for offline replay against persist's
            #     BatchEnvelope::from_json. The 337424a samples confirmed
            #     PYJSON=<accepted-by-python-json> for every reject, so
            #     the bytes are well-formed JSON; persist's typed
            #     deserialize is the gate. With a complete body we can
            #     feed it into a tiny Rust test and get the real
            #     serde_json::Error message ("missing field X at line N
            #     column M") instead of waiting on a persist release to
            #     surface detail() for Error::Json(_).
            #
            #     Strategy: if body fits in 8KB, log the WHOLE thing.
            #     Otherwise bump head + tail to 4KB each — the
            #     malformed-field error usually points near either end.
            #     Caps are bounded under AV-15; bytes already passed
            #     pydantic so syntactically reasonable JSON.
            FULL_BODY_LIMIT = 8 * 1024
            HEAD_TAIL_LIMIT = 4 * 1024
            try:
                if len(body) <= FULL_BODY_LIMIT:
                    full = body.decode("utf-8", errors="replace")
                    logger.warning(
                        "PERSIST_DELEGATE_REJECT_BODY_FULL sha256_prefix=%s body_bytes=%d full=%r",
                        body_sha, len(body), full,
                    )
                else:
                    head = body[:HEAD_TAIL_LIMIT].decode("utf-8", errors="replace")
                    tail = body[-HEAD_TAIL_LIMIT:].decode("utf-8", errors="replace")
                    logger.warning(
                        "PERSIST_DELEGATE_REJECT_BODY_HEAD sha256_prefix=%s head=%r",
                        body_sha, head,
                    )
                    logger.warning(
                        "PERSIST_DELEGATE_REJECT_BODY_TAIL sha256_prefix=%s tail=%r",
                        body_sha, tail,
                    )
            except Exception as decode_err:
                logger.warning(
                    "PERSIST_DELEGATE_REJECT_BODY_DECODE_ERR sha256_prefix=%s err=%r",
                    body_sha, decode_err,
                )
        # 401 only when verify failed because the signing key isn't in
        # the directory; everything else verify-related stays 422
        # (malformed sig, sig mismatch, etc.).
        if msg.lower().startswith("verify:") and "unknown key" in msg.lower():
            raise HTTPException(status_code=401, detail=msg) from e
        raise HTTPException(status_code=422, detail=msg) from e
    except RuntimeError as e:
        msg = str(e)
        logger.error(
            "PERSIST_DELEGATE_REJECT class=RuntimeError msg=%s body_sha256_prefix=%s body_bytes=%d",
            msg[:200], body_sha, len(body),
        )
        raise HTTPException(
            status_code=503,
            detail=msg,
            headers={"Retry-After": "5"},
        ) from e

    # Persist returned success. Surface the BatchSummary so operators
    # can see envelopes_processed / trace_events_inserted /
    # signatures_verified / etc. — this is the only signal that
    # distinguishes "delegated and persisted N rows" from "delegated
    # and silently filtered to 0 rows".
    envelopes = summary.get("envelopes_processed", -1)
    events_inserted = summary.get("trace_events_inserted", -1)
    events_conflicted = summary.get("trace_events_conflicted", -1)
    logger.info(
        "PERSIST_DELEGATE_RESULT envelopes=%d events_inserted=%d "
        "events_conflicted=%d llm_calls_inserted=%d scrubbed_fields=%d "
        "signatures_verified=%d",
        envelopes,
        events_inserted,
        events_conflicted,
        summary.get("trace_llm_calls_inserted", -1),
        summary.get("scrubbed_fields", -1),
        summary.get("signatures_verified", -1),
    )

    # Adapt persist's BatchSummary dict to the legacy AccordEventsResponse
    # shape the route's response_model expects. Without this adapter,
    # FastAPI's pydantic validation throws on the unrecognized
    # BatchSummary fields and returns HTTP 500 — making every
    # successful persist write LOOK like a server error to the agent,
    # which then retries, hits the dedup index, and spirals.
    # (Bridge surfaced this as "27x DELEGATE_RESULT, 27x HTTP 500,
    # only the first envelope wrote rows because every retry
    # conflicted on the UNIQUE index.")
    #
    # An envelope reaching this branch was successfully verified +
    # scrubbed + processed. From the agent's perspective, that's
    # accepted, regardless of whether rows landed (first write) or
    # were deduped (replay). Both states are non-error.
    accepted = max(envelopes, 0) if envelopes > 0 else 0
    return {
        "status": "ok",
        "received": max(envelopes, 0),
        "accepted": accepted,
        "rejected": 0,
        "rejected_traces": None,
        "errors": None,
    }


@router.post("/events", response_model=AccordEventsResponse)
async def receive_accord_events(
    request: AccordEventsRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """
    Receive Ed25519-signed reasoning traces from CIRIS agents.

    This endpoint implements the Coherence Ratchet receiver, accepting
    immutable records of agent decision-making for transparency and
    alignment validation.

    Phase 2a (CIRISPersist v0.1.4 cutover): when
    CIRISLENS_USE_PERSIST_ENGINE=true and Engine init succeeded, standard
    trace ingest delegates to ciris-persist's Engine.receive_and_persist
    (writes trace_events + trace_llm_calls + scrub envelope columns).
    Connectivity events (startup/shutdown) and mock-LLM traces stay on
    the legacy code path because persist doesn't model those shapes.
    Pre-cutover history in `accord_traces` is read-only from the cutover
    moment forward (FSD CIRIS_PERSIST §3.5 — no dual-write window).

    Reference: Accord Section IV - Ethical Integrity Surveillance
    """
    import base64

    # ─── Phase 2a: routing decision (always logged for diagnostics) ──
    # Emit a structured per-request trace of why delegation did or did
    # not fire. Operators tail this log to confirm cutover health
    # without having to add ad-hoc instrumentation each time.
    flag = os.environ.get("CIRISLENS_USE_PERSIST_ENGINE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    engine_present = persist_engine.get_engine() is not None
    scrubber_ready = persist_engine.scrubber_ready()
    is_connectivity = _is_connectivity_batch(request)
    is_mock = _has_mock_llm_traces(request)
    delegate = (
        flag
        and engine_present
        and (request.trace_level == "generic" or scrubber_ready)
        and not is_connectivity
        and not is_mock
    )
    logger.info(
        "PERSIST_ROUTE flag=%s engine=%s scrubber=%s level=%s "
        "connectivity=%s mock=%s delegate=%s events=%d",
        flag, engine_present, scrubber_ready, request.trace_level,
        is_connectivity, is_mock, delegate, len(request.events),
    )

    # ─── Phase 2a: try to delegate to ciris-persist Engine ───────────
    if delegate:
        body = getattr(raw_request.state, "cached_body", None)
        if body is not None:
            logger.info(
                "PERSIST_DELEGATE trace_level=%s events=%d body_bytes=%d",
                request.trace_level, len(request.events), len(body),
            )
            return await _delegate_to_persist(body)
        # Body cache missing (middleware didn't fire?) — fall through to
        # legacy path. This shouldn't happen in normal deployment; the
        # cache_request_body middleware in main.py covers /accord/events.
        logger.warning("Persist delegation skipped: cached_body absent; falling back to legacy")

    # ─── Legacy path (pre-cutover trace ingest) ──────────────────────
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Load public keys
    public_keys = await load_public_keys()
    if not public_keys:
        logger.warning("No public keys loaded - traces will be stored unverified")

    accepted = 0
    rejected = 0
    rejected_traces: list[str] = []
    errors: list[str] = []

    async with db_pool.acquire() as conn:
        for event in request.events:
            trace = event.trace

            # =================================================================
            # SCHEMA VALIDATION - First line of defense
            # Ensures trace conforms to known schema before any processing
            # =================================================================
            schema_result = validate_trace_schema(
                trace.trace_id,
                [c.model_dump() for c in trace.components],
            )

            if not schema_result.is_valid:
                # Log to malformation table for audit
                logger.warning(
                    "SCHEMA_INVALID trace %s: version=%s errors=%s",
                    trace.trace_id,
                    schema_result.schema_version.value,
                    schema_result.errors,
                )
                rejected += 1
                rejected_traces.append(trace.trace_id)
                errors.append(f"{trace.trace_id}: Schema validation failed - {schema_result.errors}")

                # Store in malformed_traces for audit
                try:
                    await conn.execute(
                        """
                        INSERT INTO cirislens.malformed_traces (
                            record_id, timestamp, trace_id,
                            detected_event_types, validation_errors, validation_warnings,
                            component_count, rejection_reason, severity
                        ) VALUES (
                            gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8
                        )
                        """,
                        datetime.now(UTC),
                        trace.trace_id,
                        schema_result.detected_event_types,
                        schema_result.errors,
                        schema_result.warnings,
                        len(trace.components),
                        f"Schema validation failed: {schema_result.errors}",
                        "warning",  # Schema mismatch is warning, not critical
                    )
                except Exception as e:
                    logger.error("Failed to log malformed trace %s: %s", trace.trace_id, e)
                continue

            # Log detected schema version for monitoring
            logger.info(
                "SCHEMA_VALID trace %s: version=%s event_types=%s",
                trace.trace_id,
                schema_result.schema_version.value,
                schema_result.detected_event_types,
            )

            # Handle connectivity events separately (startup/shutdown)
            if schema_result.schema_version == SchemaVersion.CONNECTIVITY:
                try:
                    # Extract event data from first component
                    event_type = schema_result.detected_event_types[0] if schema_result.detected_event_types else "unknown"
                    event_data = trace.components[0].data if trace.components else {}

                    await conn.execute(
                        """
                        INSERT INTO cirislens.connectivity_events (
                            timestamp, trace_id, event_type,
                            agent_id, agent_name, agent_id_hash,
                            event_data, signature, signature_key_id,
                            consent_timestamp, trace_level
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
                        )
                        """,
                        request.batch_timestamp,
                        trace.trace_id,
                        event_type,
                        event_data.get("agent_id") if isinstance(event_data, dict) else None,
                        event_data.get("agent_name") if isinstance(event_data, dict) else None,
                        trace.agent_id_hash,
                        json.dumps(event_data) if event_data else None,
                        trace.signature,
                        trace.signature_key_id,
                        request.consent_timestamp,
                        request.trace_level,
                    )
                    logger.info(
                        "CONNECTIVITY_EVENT stored: %s type=%s agent=%s",
                        trace.trace_id,
                        event_type,
                        event_data.get("agent_name") if isinstance(event_data, dict) else "unknown",
                    )
                    accepted += 1
                except Exception as e:
                    logger.error("Failed to store connectivity event %s: %s", trace.trace_id, e)
                    rejected += 1
                continue

            # Verify signature
            is_valid, error = verify_trace_signature(trace, public_keys, request.trace_level)

            # Multi-worker cache-miss recovery: Uvicorn runs N worker processes,
            # each with its own Python-global public_keys dict. A registration
            # hits one worker; traces to other workers see stale caches.
            # On "unknown signer key", do a targeted DB lookup for just this
            # key before rejecting. Populates the local worker's cache.
            if not is_valid and error and error.startswith("Unknown signer key:"):
                import base64 as _b64
                row = await conn.fetchrow(
                    """
                    SELECT public_key_base64 FROM cirislens.accord_public_keys
                    WHERE key_id = $1 AND revoked_at IS NULL
                    AND (expires_at IS NULL OR expires_at > NOW())
                    """,
                    trace.signature_key_id,
                )
                if row:
                    public_keys[trace.signature_key_id] = _b64.b64decode(
                        row["public_key_base64"]
                    )
                    logger.info(
                        "PUBLIC_KEY_CACHE_MISS_RECOVERED key_id=%s "
                        "(loaded from DB, worker-local cache was stale)",
                        trace.signature_key_id,
                    )
                    is_valid, error = verify_trace_signature(
                        trace, public_keys, request.trace_level
                    )

            if not is_valid and public_keys:
                rejected += 1
                rejected_traces.append(trace.trace_id)
                if error:
                    errors.append(f"{trace.trace_id}: {error}")
                continue

            # PII Scrubbing — FSD §1 invariant: no unscrubbed text touches
            # storage. Generic traces are scores-only (no text). Detailed
            # runs the regex pass. Full_traces runs NER + regex and gets a
            # cryptographic envelope (original-content hash + CIRISLens
            # scrub signature) so the agent's signature can still be
            # re-verified post-scrub. Must run BEFORE metadata extraction.
            pii_scrubbed = False
            original_content_hash = None
            scrub_timestamp = None
            scrub_signature = None
            scrub_key_id = None

            if request.trace_level in ("detailed", "full_traces"):
                # Hash original content before any mutation (full_traces only —
                # detailed has no signature envelope).
                if request.trace_level == "full_traces":
                    original_message = json.dumps(
                        [c.model_dump() for c in trace.components], sort_keys=True
                    ).encode('utf-8')
                    original_content_hash = hashlib.sha256(original_message).hexdigest()

                # Scrub PII from each component's data via the v1 Python
                # scrubber. This branch only fires for non-generic traces
                # routed through the LEGACY ingest path — i.e. when
                # CIRISLENS_USE_PERSIST_ENGINE is off, OR the request is
                # a connectivity/mock-LLM batch. Production trace ingest
                # uses persist's lens_scrubber callback (Phase 2a), which
                # already runs v2 + sanitizer; this branch is fallback.
                #
                # Phase 2b: scrubber_compare.py removed. Its purpose was
                # to run v1 + v2 in parallel and log divergence for R3.5
                # validation; v2 is now in production via persist's
                # callback at ~50 events/sec sustained, so the
                # comparison crutch is obsolete. v1 stays as the legacy
                # fallback.
                scrubbed_components = []
                v1_failed = False
                for comp in trace.components:
                    comp_dict = comp.model_dump()
                    if "data" in comp_dict:
                        try:
                            comp_dict["data"] = _scrub_legacy(comp_dict["data"])
                        except Exception:
                            logger.exception(
                                "Legacy v1 scrubber failed on trace %s — rejecting",
                                trace.trace_id,
                            )
                            v1_failed = True
                            break
                    scrubbed_components.append(comp_dict)

                if v1_failed:
                    rejected += 1
                    rejected_traces.append(trace.trace_id)
                    errors.append(f"{trace.trace_id}: scrubber rejected the trace")
                    continue

                # Apply scrubbed data back to trace components in place
                # so downstream metadata extraction + sanitization see the
                # scrubbed content.
                for i, comp in enumerate(trace.components):
                    if i >= len(scrubbed_components):
                        break
                    for key, value in scrubbed_components[i].get("data", {}).items():
                        if hasattr(comp, "data") and isinstance(comp.data, dict):
                            comp.data[key] = value

                pii_scrubbed = True

                if request.trace_level == "full_traces":
                    # Re-sign scrubbed content with the CIRISLens scrub key
                    # for tamper-evidence on the post-scrub artifact.
                    scrubber = get_scrubber()
                    scrub_timestamp = datetime.now(UTC)
                    scrub_key_id = scrubber.scrub_key_id
                    if scrubber._signing_key:
                        scrubbed_message = json.dumps(
                            scrubbed_components, sort_keys=True
                        ).encode('utf-8')
                        from pii_scrubber import sign_content
                        scrub_signature = sign_content(scrubbed_message, scrubber._signing_key)
                    logger.info(
                        "Scrubbed PII from full_traces %s (hash: %s...)",
                        trace.trace_id, original_content_hash[:16],
                    )
                else:
                    logger.debug("Scrubbed detailed trace %s", trace.trace_id)

            # =================================================================
            # SECURITY SANITIZATION - applies to ALL trace levels
            # Detects and neutralizes XSS, SQL injection, and other payloads
            # =================================================================
            try:
                # Sanitize trace components
                trace_dict = {"components": [c.model_dump() for c in trace.components]}
                sanitized_trace, sanitization_result = sanitize_trace_for_storage(
                    trace_dict, trace_level=request.trace_level
                )

                # Update trace components with sanitized data
                if sanitization_result.fields_modified > 0:
                    # Apply sanitized data back to trace components
                    for i, comp in enumerate(trace.components):
                        if i < len(sanitized_trace.get("components", [])):
                            sanitized_comp = sanitized_trace["components"][i]
                            if hasattr(comp, "data") and isinstance(comp.data, dict):
                                comp.data.update(sanitized_comp.get("data", {}))
                    logger.warning(
                        "SECURITY_SANITIZATION trace %s: detections=%s modified=%d",
                        trace.trace_id,
                        sanitization_result.total_detections,
                        sanitization_result.fields_modified,
                    )
            except Exception as e:
                logger.error("Security sanitization failed for %s: %s", trace.trace_id, e)
                # Continue processing - don't block on sanitization failure

            # Extract metadata from components (now scrubbed and sanitized)
            metadata = extract_trace_metadata(trace, trace_level=request.trace_level)

            # Add detected schema version (from validation above)
            metadata["schema_version"] = schema_result.schema_version.value

            # Log scoring eligibility
            if is_scoring_eligible(schema_result.schema_version):
                logger.debug(
                    "Trace %s eligible for CIRIS Scoring (schema %s)",
                    trace.trace_id, schema_result.schema_version.value
                )

            try:
                # Type conversions and validation for database compatibility

                # Validate and sanitize models_used
                validated_models, model_issues = validate_models_used(metadata["models_used"])
                if model_issues:
                    logger.debug(
                        "models_used validation issues for %s: %s",
                        trace.trace_id, model_issues
                    )
                metadata["models_used"] = validated_models

                # audit_entry_id: convert string UUID to UUID object if present
                audit_entry_id = metadata["audit_entry_id"]
                if audit_entry_id and isinstance(audit_entry_id, str):
                    try:
                        audit_entry_id = UUID(audit_entry_id)
                    except (ValueError, TypeError):
                        logger.warning("Invalid audit_entry_id format: %s", audit_entry_id)
                        audit_entry_id = None

                # models_used: ensure it's JSON serialized for JSONB column
                models_used = metadata["models_used"]
                if models_used is not None and not isinstance(models_used, str):
                    models_used = json.dumps(models_used)

                # Route mock traces to mock repository for dev/testing
                # Mock traces reaching here have already passed signature verification
                # NOTE: Generic traces don't include models_used, so mock detection only works
                # for 'detailed' or 'full_traces' level traces
                if _is_mock_trace(metadata["models_used"], trace_level=request.trace_level):
                    logger.info(
                        "ROUTING mock trace %s to mock repo (models: %s, level: %s)",
                        trace.trace_id, metadata["models_used"], request.trace_level
                    )
                    # Pass original list for TEXT[] column, not JSON string
                    models_used_list = metadata["models_used"] or []
                    await _store_mock_trace(
                        conn, trace, metadata, models_used_list,
                        request.batch_timestamp, request.consent_timestamp,
                        signature_verified=is_valid,
                    )
                    continue

                # Log trace storage attempt with level-appropriate info
                if request.trace_level == "generic":
                    # Generic traces: log scores (that's what we have)
                    logger.info(
                        "STORING trace %s: level=%s csdma=%.2f dsdma=%.2f k_eff=%.1f conscience=%s",
                        trace.trace_id,
                        request.trace_level,
                        metadata.get("csdma_plausibility_score") or 0,
                        metadata.get("dsdma_domain_alignment") or 0,
                        metadata.get("idma_k_eff") or 0,
                        metadata.get("conscience_passed"),
                    )
                else:
                    # Detailed/full traces: log identifiers
                    logger.info(
                        "STORING trace %s: level=%s agent=%s type=%s action=%s",
                        trace.trace_id,
                        request.trace_level,
                        metadata["agent_name"],
                        metadata["trace_type"],
                        metadata["selected_action"],
                    )

                # Store trace with all extracted metadata
                await conn.execute(
                    """
                    INSERT INTO cirislens.accord_traces (
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
                        entropy_level, coherence_level, uncertainty_acknowledged, reasoning_transparency,
                        updated_status_detected, thought_depth_triggered,
                        entropy_passed, coherence_passed,
                        optimization_veto_passed, epistemic_humility_passed,
                        selected_action, action_success, processing_ms,
                        audit_entry_id, audit_sequence_number, audit_entry_hash, audit_signature,
                        tokens_input, tokens_output, tokens_total,
                        cost_cents, carbon_grams, energy_mwh,
                        llm_calls, models_used,
                        signature, signature_key_id, signature_verified,
                        consent_timestamp, timestamp, trace_level,
                        original_content_hash, pii_scrubbed, scrub_timestamp,
                        scrub_signature, scrub_key_id,
                        has_positive_moment, has_execution_error, execution_time_ms,
                        selection_confidence, is_recursive, follow_up_thought_id, api_bases_used,
                        schema_version,
                        idma_result, tsaspdma_result,
                        tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved,
                        thought_start_at, snapshot_at, dma_results_at, aspdma_at,
                        idma_at, tsaspdma_at, conscience_at, action_result_at,
                        memory_count, context_tokens, conversation_turns,
                        alternatives_considered, conscience_checks_count
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
                        $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
                        $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
                        $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
                        $51, $52, $53, $54, $55, $56, $57, $58, $59, $60,
                        $61, $62, $63, $64, $65, $66, $67, $68, $69, $70,
                        $71, $72, $73, $74, $75, $76, $77, $78, $79,
                        $80, $81, $82, $83, $84, $85, $86, $87,
                        $88, $89, $90, $91, $92
                    )
                    ON CONFLICT (trace_id, timestamp) DO NOTHING
                    """,
                    trace.trace_id,                              # $1
                    metadata["thought_id"],                      # $2
                    metadata["task_id"],                         # $3
                    metadata["agent_id_hash"],                   # $4
                    metadata["agent_name"],                      # $5
                    metadata["trace_type"],                      # $6
                    metadata["cognitive_state"],                 # $7
                    metadata["thought_type"],                    # $8
                    metadata["thought_depth"],                   # $9
                    metadata["started_at"],                      # $10
                    metadata["completed_at"],                    # $11
                    json.dumps(metadata["thought_start"]),       # $12
                    json.dumps(metadata["snapshot_and_context"]),# $13
                    json.dumps(metadata["dma_results"]),         # $14
                    json.dumps(metadata["aspdma_result"]),       # $15
                    json.dumps(metadata["conscience_result"]),   # $16
                    json.dumps(metadata["action_result"]),       # $17
                    metadata["csdma_plausibility_score"],        # $18
                    metadata["dsdma_domain_alignment"],          # $19
                    metadata["dsdma_domain"],                    # $20
                    metadata["pdma_stakeholders"],               # $21
                    metadata["pdma_conflicts"],                  # $22
                    metadata["idma_k_eff"],                      # $23
                    metadata["idma_correlation_risk"],           # $24
                    metadata["idma_fragility_flag"],             # $25
                    metadata["idma_phase"],                      # $26
                    metadata["action_rationale"],                # $27
                    metadata["conscience_passed"],               # $28
                    metadata["action_was_overridden"],           # $29
                    metadata["entropy_level"],                   # $30
                    metadata["coherence_level"],                 # $31
                    metadata["uncertainty_acknowledged"],        # $32
                    metadata["reasoning_transparency"],          # $33
                    metadata["updated_status_detected"],         # $34
                    metadata["thought_depth_triggered"],         # $35
                    metadata["entropy_passed"],                  # $36
                    metadata["coherence_passed"],                # $37
                    metadata["optimization_veto_passed"],        # $38
                    metadata["epistemic_humility_passed"],       # $39
                    metadata["selected_action"],                 # $40
                    metadata["action_success"],                  # $41
                    metadata["processing_ms"],                   # $42
                    audit_entry_id,                              # $43 - converted to UUID
                    metadata["audit_sequence_number"],           # $44
                    metadata["audit_entry_hash"],                # $45
                    metadata["audit_signature"],                 # $46
                    metadata["tokens_input"],                    # $47
                    metadata["tokens_output"],                   # $48
                    metadata["tokens_total"],                    # $49
                    metadata["cost_cents"],                      # $50
                    metadata["carbon_grams"],                    # $51
                    metadata["energy_mwh"],                      # $52
                    metadata["llm_calls"],                       # $53
                    models_used,                                 # $54 - JSON serialized
                    trace.signature,                             # $55
                    trace.signature_key_id,                      # $56
                    is_valid,                                    # $57
                    request.consent_timestamp,                   # $58
                    request.batch_timestamp,                     # $59
                    metadata["trace_level"],                     # $60
                    original_content_hash,                       # $61
                    pii_scrubbed,                                # $62
                    scrub_timestamp,                             # $63
                    scrub_signature,                             # $64
                    scrub_key_id,                                # $65
                    metadata["has_positive_moment"],             # $66 - S factor scoring
                    metadata["has_execution_error"],             # $67
                    metadata["execution_time_ms"],               # $68
                    metadata["selection_confidence"],            # $69
                    metadata["is_recursive"],                    # $70
                    metadata["follow_up_thought_id"],            # $71
                    metadata["api_bases_used"],                  # $72 - array
                    metadata["schema_version"],                  # $73 - for scoring eligibility
                    json.dumps(metadata["idma_result"]),         # $74 - V1.9.3 IDMA separate event
                    json.dumps(metadata["tsaspdma_result"]),     # $75 - V1.9.3 TSASPDMA result
                    metadata["tool_name"],                       # $76 - tool name from TSASPDMA
                    json.dumps(metadata["tool_parameters"]),     # $77 - tool parameters
                    metadata["tsaspdma_reasoning"],              # $78 - TSASPDMA reasoning
                    metadata["tsaspdma_approved"],               # $79 - TSASPDMA approval status
                    metadata["thought_start_at"],                # $80 - step timestamp
                    metadata["snapshot_at"],                     # $81 - step timestamp
                    metadata["dma_results_at"],                  # $82 - step timestamp
                    metadata["aspdma_at"],                       # $83 - step timestamp
                    metadata["idma_at"],                         # $84 - step timestamp
                    metadata["tsaspdma_at"],                     # $85 - step timestamp
                    metadata["conscience_at"],                   # $86 - step timestamp
                    metadata["action_result_at"],                # $87 - step timestamp
                    metadata["memory_count"],                    # $88 - observation weight
                    metadata["context_tokens"],                  # $89 - observation weight
                    metadata["conversation_turns"],              # $90 - observation weight
                    metadata["alternatives_considered"],         # $91 - observation weight
                    metadata["conscience_checks_count"],         # $92 - observation weight
                )
                accepted += 1
                logger.info("Successfully stored trace %s", trace.trace_id)

            except Exception as e:
                # Log full error with traceback for debugging
                error_msg = str(e)
                logger.error(
                    "Failed to store trace %s: %s\nTraceback:\n%s",
                    trace.trace_id,
                    error_msg,
                    traceback.format_exc(),
                )
                # Log problematic metadata values for debugging
                logger.error(
                    "Trace %s metadata dump: started_at=%r, completed_at=%r, "
                    "audit_entry_id=%r, models_used=%r, consent_timestamp=%r, batch_timestamp=%r",
                    trace.trace_id,
                    metadata.get("started_at"),
                    metadata.get("completed_at"),
                    metadata.get("audit_entry_id"),
                    metadata.get("models_used"),
                    request.consent_timestamp,
                    request.batch_timestamp,
                )
                rejected += 1
                rejected_traces.append(trace.trace_id)
                # Include actual error message for diagnosis
                errors.append(f"{trace.trace_id}: {error_msg}")

        # Record batch metadata
        correlation_json = None
        if request.correlation_metadata:
            correlation_json = json.dumps(request.correlation_metadata.model_dump(exclude_none=True))

        await conn.execute(
            """
            INSERT INTO cirislens.accord_trace_batches (
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
    }

    if rejected_traces:
        response["rejected_traces"] = rejected_traces
    if errors:
        response["errors"] = errors

    return response


# =============================================================================
# API Endpoint - Public Key Management
# =============================================================================


class PublicKeyCreate(BaseModel):
    """Register a new public key for signature verification."""

    key_id: str
    public_key_base64: str
    description: str | None = None


@router.post("/public-keys")
async def register_public_key(
    key: PublicKeyCreate,
) -> dict[str, Any]:
    """
    Register a public key for trace signature verification.

    This is typically called once during initial setup with the
    root public key from seed/root_pub.json.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Validate the key is valid base64 and correct length for Ed25519
    import base64

    try:
        key_bytes = base64.b64decode(key.public_key_base64)
        if len(key_bytes) != 32:
            raise HTTPException(
                status_code=400, detail="Invalid Ed25519 public key length"
            )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid base64 encoding: {e}"
        ) from e

    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO cirislens.accord_public_keys (
                    key_id, public_key_base64, description
                ) VALUES ($1, $2, $3)
                ON CONFLICT (key_id) DO UPDATE
                SET public_key_base64 = $2, description = $3
                """,
                key.key_id,
                key.public_key_base64,
                key.description,
            )
        except Exception as e:
            logger.error("Failed to register public key: %s", e)
            raise HTTPException(status_code=500, detail="Failed to register key") from e

    # Invalidate cache
    global _public_keys_loaded
    _public_keys_loaded = False

    logger.info("Registered public key: %s", key.key_id)

    # v0.2.2 federation directory mirror — best-effort, never raises.
    # When the lens-steward identity is configured AND the bootstrap
    # row exists in federation_keys, this call writes a hybrid-pending
    # row (Ed25519-signed, PQC-pending) into federation_keys that
    # CIRISRegistry / peer lenses / agents querying peer keys can
    # discover. accord_public_keys remains load-bearing for verify
    # until v0.4.0; this is purely additive directory propagation.
    import federation_mirror  # lazy import — persist_engine is optional
    federation_mirror.mirror_agent_registration(
        key_id=key.key_id,
        public_key_base64=key.public_key_base64,
        description=key.description,
    )

    return {"status": "registered", "key_id": key.key_id}


@router.get("/public-keys")
async def list_public_keys() -> dict[str, Any]:
    """List registered public keys (without the actual key values)."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT key_id, algorithm, description, created_at, expires_at, revoked_at
            FROM cirislens.accord_public_keys
            ORDER BY created_at DESC
            """
        )

        keys = [
            {
                "key_id": row["key_id"],
                "algorithm": row["algorithm"],
                "description": row["description"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "revoked": row["revoked_at"] is not None,
            }
            for row in rows
        ]

        return {"keys": keys, "count": len(keys)}


# =============================================================================
# DSAR (Data Subject Access Request) - Trace Deletion
# Reference: GDPR Article 17 (Right to Erasure), Article 11(2)
# =============================================================================


class DSARDeleteRequest(BaseModel):
    """Request to delete all traces for an agent (self-service DSAR)."""

    agent_id_hash: str = Field(
        ..., min_length=8, max_length=64, description="SHA-256 hash of agent_id"
    )
    request_type: str = Field(
        default="delete_all_traces", pattern="^delete_all_traces$"
    )
    reason: str = Field(
        default="User DSAR self-service request", max_length=500
    )
    requested_at: str = Field(
        ..., description="ISO 8601 timestamp of when deletion was requested"
    )
    signature: str = Field(
        ..., description="Base64-encoded Ed25519 signature of the request payload"
    )
    signature_key_id: str = Field(
        ..., description="Key ID used to sign this request"
    )


def _verify_dsar_signature(
    request: DSARDeleteRequest, public_keys: dict[str, bytes]
) -> tuple[bool, str | None]:
    """
    Verify Ed25519 signature on a DSAR deletion request.

    The agent signs the canonical JSON of:
    {"agent_id_hash": "...", "request_type": "...", "requested_at": "..."}

    Returns (is_valid, error_message).
    """
    import base64

    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        logger.error("PyNaCl not installed - cannot verify DSAR signatures")
        return False, "Signature verification unavailable"

    if request.signature_key_id not in public_keys:
        return False, f"Unknown signer key: {request.signature_key_id}"

    try:
        # Decode signature
        sig_str = request.signature
        padding_needed = 4 - (len(sig_str) % 4)
        if padding_needed != 4:
            sig_str += "=" * padding_needed
        try:
            signature = base64.urlsafe_b64decode(sig_str)
        except Exception:
            signature = base64.b64decode(sig_str)

        # Construct canonical message (matches agent-side format)
        signed_payload = {
            "agent_id_hash": request.agent_id_hash,
            "request_type": request.request_type,
            "requested_at": request.requested_at,
        }
        message = json.dumps(
            signed_payload, sort_keys=True, separators=(",", ":")
        ).encode()

        verify_key = VerifyKey(public_keys[request.signature_key_id])
        verify_key.verify(message, signature)
        return True, None

    except BadSignatureError:
        return False, "Invalid signature"
    except Exception as e:
        logger.error("DSAR signature verification error: %s", e)
        return False, f"Verification error: {e}"


@router.post("/dsar/delete")
async def dsar_delete_traces(request: DSARDeleteRequest) -> dict[str, Any]:
    """
    Delete all traces for an agent (self-service DSAR endpoint).

    The request must be signed with the same Ed25519 key the agent uses
    to sign traces, proving the deletion request comes from the actual
    agent that submitted the data.

    IMPORTANT: Only traces signed by the requesting key are deleted.
    This prevents cross-agent deletion when multiple agents share
    the same agent_id_hash.

    Returns:
    - 200: Traces deleted successfully
    - 202: Deletion request queued for processing
    - 404: No traces found for this agent_id_hash/key combination
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Load public keys and verify signature
    public_keys = await load_public_keys()
    sig_valid, sig_error = _verify_dsar_signature(request, public_keys)

    if not sig_valid:
        raise HTTPException(
            status_code=403,
            detail=f"Signature verification failed: {sig_error}",
        )

    async with db_pool.acquire() as conn:
        # Record the DSAR request for audit trail
        await conn.execute(
            """
            INSERT INTO cirislens.dsar_requests (
                agent_id_hash, request_type, reason, requested_at,
                signature, signature_key_id, signature_verified, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'processing')
            """,
            request.agent_id_hash,
            request.request_type,
            request.reason,
            datetime.fromisoformat(request.requested_at),
            request.signature,
            request.signature_key_id,
            sig_valid,
        )

        # Count traces before deletion - only traces signed by this key
        trace_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM cirislens.accord_traces
            WHERE agent_id_hash = $1 AND signature_key_id = $2
            """,
            request.agent_id_hash,
            request.signature_key_id,
        )

        if trace_count == 0:
            # Check mock traces too
            mock_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM cirislens.accord_traces_mock
                WHERE agent_id_hash = $1 AND signature_key_id = $2
                """,
                request.agent_id_hash,
                request.signature_key_id,
            )

            # Update DSAR record
            await conn.execute(
                """
                UPDATE cirislens.dsar_requests
                SET status = 'completed', traces_deleted = 0,
                    processed_at = NOW()
                WHERE id = (
                    SELECT id FROM cirislens.dsar_requests
                    WHERE agent_id_hash = $1
                    AND status = 'processing'
                    ORDER BY created_at DESC LIMIT 1
                )
                """,
                request.agent_id_hash,
            )

            if mock_count == 0:
                return {
                    "status": "not_found",
                    "message": "No traces found for this agent_id_hash signed by this key",
                    "agent_id_hash": request.agent_id_hash,
                    "signature_key_id": request.signature_key_id,
                    "traces_deleted": 0,
                }

        # Delete from accord_traces - only traces signed by this key
        result = await conn.execute(
            """
            DELETE FROM cirislens.accord_traces
            WHERE agent_id_hash = $1 AND signature_key_id = $2
            """,
            request.agent_id_hash,
            request.signature_key_id,
        )
        deleted_traces = int(result.split()[-1]) if result else 0

        # Delete from accord_traces_mock - only traces signed by this key
        result = await conn.execute(
            """
            DELETE FROM cirislens.accord_traces_mock
            WHERE agent_id_hash = $1 AND signature_key_id = $2
            """,
            request.agent_id_hash,
            request.signature_key_id,
        )
        deleted_mock = int(result.split()[-1]) if result else 0

        # Delete from coherence_ratchet_alerts related to this agent
        # Note: alerts are system-generated, not signed by agents, so we only
        # filter by agent_id_hash here. This is acceptable because alerts are
        # derived from traces - if the traces are gone, alerts should go too.
        result = await conn.execute(
            """
            DELETE FROM cirislens.coherence_ratchet_alerts
            WHERE agent_id_hash = $1
            """,
            request.agent_id_hash,
        )
        deleted_alerts = int(result.split()[-1]) if result else 0

        # ─── Persist-owned data: post-Phase-2a writes go to trace_events ──
        # CIRISLens#8 ASK 1 closure (CIRISPersist v0.3.6, after #15
        # added the required signature_key_id parameter that preserves
        # the per-key authorization scope this handler enforces).
        # Engine deletes trace_events + trace_llm_calls (with cascade)
        # filtered by the SAME (agent_id_hash, signature_key_id) tuple
        # we apply to the lens-owned legacy tables above. include_
        # federation_key=False — leaves the agent's signing key alive
        # so they can register fresh consent + start a new corpus,
        # matching pre-fold lens behaviour.
        deleted_trace_events = 0
        deleted_trace_llm_calls = 0
        engine = persist_engine.get_engine()
        if engine is not None:
            try:
                engine_summary = engine.delete_traces_for_agent(
                    request.agent_id_hash,
                    request.signature_key_id,
                    include_federation_key=False,
                )
                deleted_trace_events = engine_summary.get("trace_events_deleted", 0)
                deleted_trace_llm_calls = engine_summary.get(
                    "trace_llm_calls_deleted", 0,
                )
                logger.info(
                    "DSAR persist-owned delete: agent_id_hash=%s key=%s "
                    "trace_events=%d trace_llm_calls=%d",
                    request.agent_id_hash, request.signature_key_id,
                    deleted_trace_events, deleted_trace_llm_calls,
                )
            except Exception as e:
                # Best-effort: lens-owned deletion already succeeded;
                # don't roll back the DSAR if the persist-side delete
                # fails. The operator can re-run the DSAR or escalate;
                # the partial deletion is recorded in the response below.
                logger.warning(
                    "DSAR persist-owned delete failed for agent_id_hash=%s "
                    "key=%s: %s (lens-owned deletes succeeded; persist-owned "
                    "data remains — operator should re-run DSAR or escalate)",
                    request.agent_id_hash, request.signature_key_id, e,
                )

        total_deleted = (
            deleted_traces + deleted_mock + deleted_trace_events
        )

        # Update DSAR record with results
        await conn.execute(
            """
            UPDATE cirislens.dsar_requests
            SET status = 'completed', traces_deleted = $2,
                processed_at = NOW()
            WHERE agent_id_hash = $1
            AND status = 'processing'
            """,
            request.agent_id_hash,
            total_deleted,
        )

        logger.info(
            "DSAR deletion completed: agent_id_hash=%s key_id=%s "
            "accord_traces=%d mock=%d alerts=%d "
            "trace_events=%d trace_llm_calls=%d",
            request.agent_id_hash,
            request.signature_key_id,
            deleted_traces,
            deleted_mock,
            deleted_alerts,
            deleted_trace_events,
            deleted_trace_llm_calls,
        )

        return {
            "status": "deleted",
            "message": "All traces deleted successfully",
            "agent_id_hash": request.agent_id_hash,
            "traces_deleted": total_deleted,
            "details": {
                # Lens-owned legacy storage (pre-Phase-2a-cutover history)
                "accord_traces": deleted_traces,
                "mock_traces": deleted_mock,
                # Lens-derived (analytical output)
                "alerts_cleared": deleted_alerts,
                # Persist-owned (post-Phase-2a writes; deleted via Engine)
                "trace_events": deleted_trace_events,
                "trace_llm_calls": deleted_trace_llm_calls,
            },
        }


# =============================================================================
# Trace Repository API - RBAC Access Control
# Reference: FSD/trace_repository_api.md
# =============================================================================


class AccessLevel(str, Enum):
    """Access levels for trace repository."""

    FULL = "full"  # Internal/admin - all traces, all fields
    PARTNER = "partner"  # Own agents + samples + partner-tagged
    PUBLIC = "public"  # Public samples only


class TraceAccessContext(BaseModel):
    """Context for trace access control."""

    access_level: AccessLevel
    user_id: str
    agent_scope: list[str] = []  # Agent IDs the user owns (partner)
    partner_id: str | None = None  # Partner ID for partner-tagged access


class PublicSampleRequest(BaseModel):
    """Request to mark a trace as public sample."""

    public_sample: bool
    reason: str | None = None


class PartnerAccessRequest(BaseModel):
    """Request to modify partner access for a trace."""

    partner_ids: list[str]
    action: str = "add"  # add, remove, set


class TraceRepositoryResponse(BaseModel):
    """Response for trace repository queries."""

    traces: list[dict[str, Any]]
    pagination: dict[str, Any]


class TraceStatisticsResponse(BaseModel):
    """Response for aggregate statistics."""

    period: dict[str, str]
    totals: dict[str, int]
    scores: dict[str, dict[str, float]]
    conscience: dict[str, Any]
    actions: dict[str, Any]
    fragility: dict[str, Any]
    by_domain: list[dict[str, Any]] | None = None


def build_access_scope_filter(
    ctx: TraceAccessContext,
    param_idx: int,
) -> tuple[str, list[Any], int]:
    """
    Build SQL WHERE clause for access control scoping.

    Returns (sql_fragment, params, next_param_idx)
    """
    if ctx.access_level == AccessLevel.FULL:
        # Full access - no restrictions
        return "", [], param_idx

    elif ctx.access_level == AccessLevel.PUBLIC:
        # Public - only public samples
        return " AND public_sample = TRUE", [], param_idx

    elif ctx.access_level == AccessLevel.PARTNER:
        # Partner - own agents + public samples + partner-tagged
        params = []
        conditions = []

        if ctx.agent_scope:
            conditions.append(f"agent_id_hash = ANY(${param_idx})")
            params.append(ctx.agent_scope)
            param_idx += 1

        conditions.append("public_sample = TRUE")

        if ctx.partner_id:
            conditions.append(f"${param_idx} = ANY(partner_access)")
            params.append(ctx.partner_id)
            param_idx += 1

        sql = f" AND ({' OR '.join(conditions)})"
        return sql, params, param_idx

    return "", [], param_idx


def filter_trace_fields(
    trace: dict[str, Any],
    access_level: AccessLevel,
) -> dict[str, Any]:
    """Filter trace fields based on access level."""
    # Full access gets everything
    if access_level == AccessLevel.FULL:
        return trace

    # Partner gets most fields except raw prompts and audit internals
    if access_level == AccessLevel.PARTNER:
        excluded = {"audit_signature", "scrub_signature", "scrub_key_id"}
        # Also strip prompts from DMA results
        filtered = {k: v for k, v in trace.items() if k not in excluded}
        if filtered.get("dma_results"):
            dma = filtered["dma_results"].copy() if isinstance(filtered["dma_results"], dict) else filtered["dma_results"]
            if isinstance(dma, dict):
                for key in dma:
                    if isinstance(dma[key], dict) and "prompt_used" in dma[key]:
                        dma[key] = {k: v for k, v in dma[key].items() if k != "prompt_used"}
                filtered["dma_results"] = dma
        return filtered

    # Public gets full details for sample traces (no field filtering)
    return trace


# =============================================================================
# API Endpoint - Trace Repository
# =============================================================================


@router.get("/repository/traces")
async def list_repository_traces(
    cursor: str | None = None,
    limit: int = 100,
    agent_id_hash: str | None = None,
    agent_name: str | None = None,
    deployment_domain: str | None = None,
    deployment_type: str | None = None,
    schema_version: str | None = None,
    cognitive_state: str | None = None,
    signature_verified: bool | None = True,
) -> dict[str, Any]:
    """List trace summaries via persist's §A read primitive.

    Pass-through of CIRISPersist v0.5.0
    ``Engine.list_trace_summaries`` (CIRISPersist#23). Returns the
    typed ``TraceListPage`` shape verbatim:

    ``{"items": [TraceSummary, ...], "next_cursor": Optional[TraceCursor]}``

    Cursor pagination: pass ``cursor`` (opaque JSON string from the
    previous response's ``next_cursor`` field) to fetch the next page.
    No ``offset`` — newest-first triage by ``started_at DESC,
    trace_id DESC``.

    Deferred (out of scope for v0.5.0; per CIRISPersist#23 §"deferred"
    and §"out of scope"):
      - Task-grouped shape (``{"tasks": [...]}``) — lives in §C
        (v0.5.1); consumers group by ``task_id`` in JS for now.
      - RBAC scoping (access_level / partner_id / agent_scope /
        public_sample) — re-emerges via a separate lens-owned curation
        table joined against ``trace_id`` when/if needed. Pre-v0.5.0
        callers that passed these are accepted (FastAPI ignores
        unknown query params) but the parameters no longer filter.
      - Numeric / threshold filters (min_plausibility, max_plausibility,
        conscience_passed, action_overridden, fragility_flag,
        trace_type, start_time, end_time) — these were applied at the
        legacy SQL layer; consumers run them client-side on the
        returned ``TraceSummary`` items, or wait for persist to expose
        the corresponding filter knobs on ``TraceFilter``.
    """
    engine = persist_engine.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="persist engine unavailable")

    trace_filter: dict[str, Any] = {}
    if agent_id_hash is not None:
        trace_filter["agent_id_hash"] = agent_id_hash
    if agent_name is not None:
        trace_filter["agent_name"] = agent_name
    if deployment_domain is not None:
        trace_filter["deployment_domain"] = deployment_domain
    if deployment_type is not None:
        trace_filter["deployment_type"] = deployment_type
    if schema_version is not None:
        trace_filter["schema_version"] = schema_version
    if cognitive_state is not None:
        trace_filter["cognitive_state"] = cognitive_state
    if signature_verified is not None:
        trace_filter["signature_verified"] = signature_verified

    try:
        page_json = engine.list_trace_summaries(
            json.dumps(trace_filter),
            cursor,
            min(limit, 1000),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_trace_summaries failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e

    return json.loads(page_json)


async def _list_repository_traces_legacy(
    # Access control (normally from JWT, here as query params for flexibility)
    access_level: AccessLevel = AccessLevel.PUBLIC,
    user_id: str = "anonymous",
    agent_scope: str | None = None,  # Comma-separated agent IDs
    partner_id: str | None = None,
    # Filtering
    agent_id: str | None = None,
    domain: str | None = None,
    trace_type: str | None = None,
    cognitive_state: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    min_plausibility: float | None = None,
    max_plausibility: float | None = None,
    conscience_passed: bool | None = None,
    action_overridden: bool | None = None,
    fragility_flag: bool | None = None,
    # Grouping
    group_by_task: bool = True,
    # Pagination
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """LEGACY pre-persist-v0.5.0 implementation, kept as reference only.

    Dead code as of stage-2 of the persist v0.5.0 migration
    (CIRISPersist#23 / CIRISLens#10). Reads from ``cirislens.accord_traces``
    which has had zero new rows since the persist 0.4.x ingest cutover.

    Will be deleted once stage-2 burns in and we're confident the
    persist §A path covers every consumer this function used to serve.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Build access context
    ctx = TraceAccessContext(
        access_level=access_level,
        user_id=user_id,
        agent_scope=agent_scope.split(",") if agent_scope else [],
        partner_id=partner_id,
    )

    # Base query with all fields for full details
    query = """
        SELECT trace_id, timestamp, agent_name, agent_id_hash,
               thought_id, task_id, trace_type, trace_level,
               cognitive_state, thought_type, thought_depth,
               started_at, completed_at,
               csdma_plausibility_score, dsdma_domain_alignment, dsdma_domain,
               pdma_stakeholders, pdma_conflicts, action_rationale,
               selected_action, action_success, action_was_overridden,
               idma_k_eff, idma_correlation_risk, idma_fragility_flag, idma_phase,
               conscience_passed, entropy_passed, coherence_passed,
               optimization_veto_passed, epistemic_humility_passed,
               entropy_level, coherence_level,
               tokens_total, cost_cents, models_used,
               dma_results, conscience_result, snapshot_and_context,
               signature_verified, pii_scrubbed, original_content_hash,
               audit_entry_id, audit_sequence_number, audit_entry_hash,
               public_sample, partner_access
        FROM cirislens.accord_traces
        WHERE 1=1
    """
    params: list[Any] = []
    param_idx = 1

    # Apply access control scoping
    scope_sql, scope_params, param_idx = build_access_scope_filter(ctx, param_idx)
    query += scope_sql
    params.extend(scope_params)

    # Apply filters
    if agent_id:
        query += f" AND agent_id_hash = ${param_idx}"
        params.append(agent_id)
        param_idx += 1

    if domain:
        query += f" AND dsdma_domain = ${param_idx}"
        params.append(domain)
        param_idx += 1

    if trace_type:
        query += f" AND trace_type = ${param_idx}"
        params.append(trace_type)
        param_idx += 1

    if cognitive_state:
        query += f" AND cognitive_state = ${param_idx}"
        params.append(cognitive_state)
        param_idx += 1

    if start_time:
        query += f" AND timestamp >= ${param_idx}"
        params.append(start_time)
        param_idx += 1

    if end_time:
        query += f" AND timestamp <= ${param_idx}"
        params.append(end_time)
        param_idx += 1

    if min_plausibility is not None:
        query += f" AND csdma_plausibility_score >= ${param_idx}"
        params.append(min_plausibility)
        param_idx += 1

    if max_plausibility is not None:
        query += f" AND csdma_plausibility_score <= ${param_idx}"
        params.append(max_plausibility)
        param_idx += 1

    if conscience_passed is not None:
        query += f" AND conscience_passed = ${param_idx}"
        params.append(conscience_passed)
        param_idx += 1

    if action_overridden is not None:
        query += f" AND action_was_overridden = ${param_idx}"
        params.append(action_overridden)
        param_idx += 1

    if fragility_flag is not None:
        query += f" AND idma_fragility_flag = ${param_idx}"
        params.append(fragility_flag)
        param_idx += 1

    # Add pagination
    safe_limit = min(limit, 1000)
    query += f" ORDER BY timestamp DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([safe_limit, offset])

    async with db_pool.acquire() as conn:
        # Get total count
        count_result = await conn.fetchval(
            f"SELECT COUNT(*) FROM cirislens.accord_traces WHERE 1=1{scope_sql}"
            + (f" AND agent_id_hash = ${len(scope_params) + 1}" if agent_id else ""),
            *scope_params,
            *([agent_id] if agent_id else []),
        )

        rows = await conn.fetch(query, *params)

        traces = []
        for row in rows:
            trace = {
                "trace_id": row["trace_id"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                "agent": {
                    "name": row["agent_name"],
                    "id_hash": row["agent_id_hash"],
                    "domain": row["dsdma_domain"],
                },
                "thought": {
                    "thought_id": row["thought_id"],
                    "task_id": row["task_id"],
                    "type": row["thought_type"],
                    "depth": row["thought_depth"],
                    "cognitive_state": row["cognitive_state"],
                },
                "action": {
                    "selected": row["selected_action"],
                    "success": row["action_success"],
                    "was_overridden": row["action_was_overridden"],
                    "rationale": row["action_rationale"],
                },
                "scores": {
                    "csdma_plausibility": float(row["csdma_plausibility_score"]) if row["csdma_plausibility_score"] else None,
                    "dsdma_alignment": float(row["dsdma_domain_alignment"]) if row["dsdma_domain_alignment"] else None,
                    "idma_k_eff": float(row["idma_k_eff"]) if row["idma_k_eff"] else None,
                    "idma_fragility": row["idma_fragility_flag"],
                    "idma_phase": row["idma_phase"],
                },
                "conscience": {
                    "passed": row["conscience_passed"],
                    "entropy_passed": row["entropy_passed"],
                    "coherence_passed": row["coherence_passed"],
                    "optimization_veto_passed": row["optimization_veto_passed"],
                    "epistemic_humility_passed": row["epistemic_humility_passed"],
                },
                "dma_results": row["dma_results"],
                "resources": {
                    "tokens_total": row["tokens_total"],
                    "cost_cents": float(row["cost_cents"]) if row["cost_cents"] else None,
                    "models_used": row["models_used"],
                },
                "provenance": {
                    "signature_verified": row["signature_verified"],
                    "pii_scrubbed": row["pii_scrubbed"],
                    "original_content_hash": row["original_content_hash"],
                },
                "audit": {
                    "entry_id": str(row["audit_entry_id"]) if row["audit_entry_id"] else None,
                    "sequence_number": row["audit_sequence_number"],
                    "entry_hash": row["audit_entry_hash"],
                },
                # Internal: used for extracting initial observation
                "_snapshot_and_context": row["snapshot_and_context"],
            }

            # Filter fields based on access level
            filtered_trace = filter_trace_fields(trace, access_level)
            # Keep internal fields for now - they're needed for grouping
            traces.append(filtered_trace)

        # Group by task_id if requested
        if group_by_task and traces:
            tasks: dict[str, dict[str, Any]] = {}
            for trace in traces:
                task_id = trace.get("thought", {}).get("task_id")
                if not task_id:
                    # Traces without task_id go into a "standalone" group
                    task_id = f"standalone-{trace['trace_id']}"

                if task_id not in tasks:
                    tasks[task_id] = {
                        "task_id": task_id,
                        "initial_observation": None,
                        "traces": [],
                    }

                # Extract initial observation from seed trace (depth 0)
                depth = trace.get("thought", {}).get("depth", 0)
                if depth == 0:
                    # Try to extract initial observation from snapshot_and_context
                    initial_obs = None
                    snapshot_ctx = trace.get("_snapshot_and_context")
                    if snapshot_ctx:
                        try:
                            ctx_data = snapshot_ctx if isinstance(snapshot_ctx, dict) else json.loads(snapshot_ctx)
                            # Path: system_snapshot.current_thought_summary.content
                            system_snapshot = ctx_data.get("system_snapshot", {})
                            thought_summary = system_snapshot.get("current_thought_summary", {})
                            content = thought_summary.get("content", "")
                            # Extract the user's question - typically after "said:" and before newline
                            if "said:" in content:
                                start = content.find("said:") + 5
                                end = content.find("\n", start)
                                if end > start:
                                    initial_obs = content[start:end].strip()
                            # Fallback: use first line if no "said:" pattern
                            if not initial_obs and content:
                                first_line = content.split("\n")[0]
                                initial_obs = first_line[:500]  # Limit length
                        except (json.JSONDecodeError, TypeError, KeyError):
                            pass
                    # Final fallback: use action rationale
                    if not initial_obs:
                        initial_obs = trace.get("action", {}).get("rationale")
                    tasks[task_id]["initial_observation"] = initial_obs
                    # Also capture agent and timestamp from seed
                    tasks[task_id]["agent"] = trace.get("agent")
                    tasks[task_id]["started_at"] = trace.get("timestamp")

                # Remove internal field before adding to output
                trace_copy = {k: v for k, v in trace.items() if not k.startswith("_")}
                tasks[task_id]["traces"].append(trace_copy)

            # Sort tasks by their earliest trace timestamp
            sorted_tasks = sorted(
                tasks.values(),
                key=lambda t: t.get("started_at") or "",
                reverse=True,
            )

            return {
                "tasks": sorted_tasks,
                "pagination": {
                    "total": count_result or 0,
                    "limit": safe_limit,
                    "offset": offset,
                    "has_more": (offset + len(traces)) < (count_result or 0),
                    "task_count": len(sorted_tasks),
                },
            }

        # Strip internal fields for non-grouped response
        clean_traces = [
            {k: v for k, v in t.items() if not k.startswith("_")}
            for t in traces
        ]
        return {
            "traces": clean_traces,
            "pagination": {
                "total": count_result or 0,
                "limit": safe_limit,
                "offset": offset,
                "has_more": (offset + len(traces)) < (count_result or 0),
            },
        }


@router.get("/repository/traces/{trace_id}")
async def get_repository_trace(trace_id: str) -> dict[str, Any]:
    """Get full trace detail via persist's §B read primitive.

    Pass-through of CIRISPersist v0.5.0 ``Engine.get_trace_detail``
    (CIRISPersist#23). Returns the typed ``TraceDetail`` shape verbatim:

    ``{"summary": TraceSummary, "components": [TraceComponentRow, ...],
        "llm_calls": [TraceLlmCallRow, ...], "envelope": TraceEnvelopeRefs}``

    404 when persist has no rows for ``trace_id``. AV-9 caveat: this
    primitive does not authenticate the caller; trace_id is the
    lookup key and the returned envelope carries ``agent_id_hash`` so
    upstream layers (auth middleware, partner-scoping, etc.) can
    authorize at the request boundary.

    Deferred RBAC (access_level / partner_id / agent_scope) — same
    framing as the listing endpoint above; pre-v0.5.0 callers passing
    these are accepted but the params no longer filter.
    """
    engine = persist_engine.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="persist engine unavailable")

    try:
        detail_json = engine.get_trace_detail(trace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("get_trace_detail failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e

    if detail_json is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    return json.loads(detail_json)


# =============================================================================
# CIRISPersist v0.5.0 §E + §F primitive pass-throughs
# =============================================================================
#
# Federation-uniform observability primitives. Thin pass-throughs of the
# typed read surface CIRISPersist#23 / v0.5.0 exposed: §F Coherence Ratchet
# detection inputs (cross_agent_divergence / temporal_drift /
# hash_chain_gaps / conscience_override_rates) and §E scoring factor
# aggregates. Same hostable-by-any-persist-deployment contract §A/§B
# established. Sovereign-mode agents reading their own corpus hit the
# same endpoints against their own persist; lens-tier deployments serve
# the federation projection.
#
# AV-15 (FFI sanitization) holds end-to-end: persist returns stable kind
# tokens on the FFI; we re-raise as HTTPException 400/503 per the
# existing _delegate_to_persist mapping.
#
# AV-43 (read-side adversary inference attack): aggregates return
# computed statistics with sample_count fields surfaced — callers gate
# on k-anonymity at their layer; persist's substrate returns counts
# truthfully.


def _window_pair_jsons(
    scoring_hours: float, baseline_hours: float | None,
) -> tuple[str, str | None]:
    """Build a contiguous window pair anchored at a single `now`.

    Returns ``(scoring_json, baseline_json)`` where the baseline ends
    exactly where the scoring window begins — required for persist's
    ``temporal_drift`` and ``aggregate_scoring_factors`` to produce a
    coherent (no-gap, no-overlap) two-window comparison.
    """
    until = datetime.now(UTC)
    scoring_since = until - timedelta(hours=scoring_hours)
    scoring_json = json.dumps({"since": scoring_since.isoformat(), "until": until.isoformat()})
    baseline_json: str | None = None
    if baseline_hours is not None:
        baseline_since = scoring_since - timedelta(hours=baseline_hours)
        baseline_json = json.dumps(
            {"since": baseline_since.isoformat(), "until": scoring_since.isoformat()},
        )
    return scoring_json, baseline_json


def _hours_to_window_json(hours: float) -> str:
    """Build a TimeWindow JSON envelope `[now - hours, now)`."""
    window_json, _ = _window_pair_jsons(hours, None)
    return window_json


def _engine_or_503():
    """Resolve the persist Engine or raise 503 — single place for the
    'persist not initialized' surface so each endpoint stays tight."""
    engine = persist_engine.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="persist engine unavailable")
    return engine


@router.get("/ratchet/divergence")
async def ratchet_cross_agent_divergence(
    deployment_domain: str,
    metric: str = "csdma_plausibility",
    hours: float = 168.0,
) -> dict[str, Any]:
    """Cross-agent divergence z-scores within a deployment domain.

    Pass-through of CIRISPersist v0.5.0 §F
    ``Engine.cross_agent_divergence``. Returns one ``DivergenceRow``
    per agent in the domain with sample_count >= persist's threshold
    and a non-trivial z-score on the requested metric.

    Args:
      deployment_domain: cohort to compare within
      metric: one of ``csdma_plausibility`` / ``dsdma_domain_alignment``
              / ``idma_k_eff`` / ``idma_correlation_risk`` /
              ``conscience_override_rate``
      hours: window size; default 168h (7d) matches the legacy
              detection scheduler's cross-agent cadence
    """
    engine = _engine_or_503()
    try:
        rows_json = engine.cross_agent_divergence(
            deployment_domain,
            _hours_to_window_json(hours),
            metric,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("cross_agent_divergence failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"rows": json.loads(rows_json)}


@router.get("/ratchet/temporal-drift")
async def ratchet_temporal_drift(
    agent_id_hash: str,
    baseline_hours: float = 168.0,
    comparison_hours: float = 24.0,
) -> dict[str, Any]:
    """Welch z-score drift between baseline and comparison windows for
    one agent.

    Pass-through of CIRISPersist v0.5.0 §F ``Engine.temporal_drift``.
    The comparison window is the trailing ``comparison_hours``; the
    baseline window ends where comparison begins and extends
    ``baseline_hours`` further back. Returns one ``TemporalDriftRow``
    per metric with a non-trivial mean shift.
    """
    engine = _engine_or_503()
    comparison_json, baseline_json = _window_pair_jsons(comparison_hours, baseline_hours)
    try:
        rows_json = engine.temporal_drift(agent_id_hash, baseline_json, comparison_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("temporal_drift failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"rows": json.loads(rows_json)}


@router.get("/ratchet/hash-chain-gaps")
async def ratchet_hash_chain_gaps(
    agent_id_hash: str,
    hours: float = 168.0,
) -> dict[str, Any]:
    """Audit-chain gaps for one agent over a window.

    Pass-through of CIRISPersist v0.5.0 §F ``Engine.hash_chain_gaps``.
    A ``HashChainGap`` row marks a discontinuity in
    ``audit_sequence_number`` (computed via LAG window function).
    Empty list = no gaps observed in the window (the integrity-
    invariant case).
    """
    engine = _engine_or_503()
    try:
        rows_json = engine.hash_chain_gaps(agent_id_hash, _hours_to_window_json(hours))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("hash_chain_gaps failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"rows": json.loads(rows_json)}


@router.get("/ratchet/override-rates")
async def ratchet_conscience_override_rates(
    deployment_domain: str,
    hours: float = 168.0,
) -> dict[str, Any]:
    """Per-agent conscience-override rates within a deployment domain.

    Pass-through of CIRISPersist v0.5.0 §F
    ``Engine.conscience_override_rates``. Returns one
    ``OverrideRateRow`` per agent in the domain with the agent's
    own rate, the domain population-weighted average, and the
    multiple-of-domain-avg signal the detection scheduler thresholds
    on.
    """
    engine = _engine_or_503()
    try:
        rows_json = engine.conscience_override_rates(
            deployment_domain,
            _hours_to_window_json(hours),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("conscience_override_rates failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"rows": json.loads(rows_json)}


@router.get("/scoring/factors/{agent_id_hash}")
async def scoring_aggregate_factors(
    agent_id_hash: str,
    hours: float = 168.0,
    baseline_hours: float | None = None,
) -> dict[str, Any]:
    """CIRIS Capacity Score factor inputs for one agent.

    Pass-through of CIRISPersist v0.5.0 §E
    ``Engine.aggregate_scoring_factors``. Returns
    ``ScoringFactorAggregate`` with the C / I_int / R / I_inc / S
    factor inputs in one DB round-trip — the composition formula
    lives in lens (``api/ciris_scoring.py``); persist exposes the
    canonical inputs.

    Args:
      agent_id_hash: the SHA-256 agent identity hash
      hours: scoring window size (default 168h = 7d, matches lens's
              default MIN_DAYS_FOR_BASELINE)
      baseline_hours: when set, persist computes ``drift_z_score``
              against a baseline window of this size, ending where
              the scoring window begins. When None, ``drift_z_score``
              is null.
    """
    engine = _engine_or_503()
    window_json, baseline_json = _window_pair_jsons(hours, baseline_hours)
    try:
        agg_json = engine.aggregate_scoring_factors(agent_id_hash, window_json, baseline_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("aggregate_scoring_factors failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(agg_json)


class ScoringBatchRequest(BaseModel):
    """Body for POST /scoring/factors/batch."""

    agent_id_hashes: list[str]
    hours: float = 168.0
    baseline_hours: float | None = None


@router.post("/scoring/factors/batch")
async def scoring_aggregate_factors_batch(
    request: ScoringBatchRequest,
) -> dict[str, Any]:
    """Fleet-wide scoring sweep — one round-trip per N agents, not N.

    Pass-through of CIRISPersist v0.5.0 §E
    ``Engine.aggregate_scoring_factors_batch``. Returns the input-
    order list of ``ScoringFactorAggregate``. See the single-agent
    endpoint above for ``hours`` / ``baseline_hours`` semantics.
    """
    engine = _engine_or_503()
    window_json, baseline_json = _window_pair_jsons(request.hours, request.baseline_hours)
    try:
        rows_json = engine.aggregate_scoring_factors_batch(
            json.dumps(request.agent_id_hashes),
            window_json,
            baseline_json,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("aggregate_scoring_factors_batch failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"aggregates": json.loads(rows_json)}


# =============================================================================
# CIRISPersist v0.5.5/v0.5.8 §C / §D / §G / §H / §I primitive pass-throughs
# =============================================================================
#
# Same idiom as the §E/§F pass-throughs above: JSON-strings-in/out, time
# windows built once via _hours_to_window_json, errors mapped through the
# typed ValueError → 400 / RuntimeError → 503 discipline. Federation-uniform
# primitive surface — same shape lens-tier and sovereign-mode agents
# consume.
#
# These endpoints are the executable spec the lens-core Rust port will
# mirror. Each is a thin wrapper around one persist read primitive.


# ── §C — task-grouped listing ───────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    cursor: str | None = None,
    limit: int = 50,
    hours: float | None = None,
    agent_id_hash: str | None = None,
    agent_name: str | None = None,
    task_class: str | None = None,
) -> dict[str, Any]:
    """Task-grouped trace listing via persist's §C ``list_tasks``.

    Canonical ``TaskClass`` derivation (qa_eval / discord /
    real_user_* / wakeup_ritual / other) lives server-side in
    persist via ``TaskClass::from_task_id`` so federation peers
    agree on task identity uniformly. ``initial_observation`` is
    extracted server-side from the earliest THOUGHT_START in each
    task.
    """
    engine = _engine_or_503()
    task_filter: dict[str, Any] = {}
    if hours is not None:
        task_filter["time_window"] = json.loads(_hours_to_window_json(hours))
    if agent_id_hash is not None:
        task_filter["agent_id_hash"] = agent_id_hash
    if agent_name is not None:
        task_filter["agent_name"] = agent_name
    if task_class is not None:
        task_filter["task_class"] = task_class
    try:
        page_json = engine.list_tasks(json.dumps(task_filter), cursor, min(limit, 200))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_tasks failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(page_json)


# ── §D — LLM call surface ───────────────────────────────────────────

@router.get("/llm-calls")
async def list_llm_calls(
    cursor: str | None = None,
    limit: int = 100,
    hours: float | None = None,
    agent_id_hash: str | None = None,
    model: str | None = None,
    status: str | None = None,
    trace_id: str | None = None,
    thought_id: str | None = None,
) -> dict[str, Any]:
    """Page through ``trace_llm_calls`` via §D ``list_llm_calls``."""
    engine = _engine_or_503()
    llm_filter: dict[str, Any] = {}
    if hours is not None:
        llm_filter["time_window"] = json.loads(_hours_to_window_json(hours))
    if agent_id_hash is not None:
        llm_filter["agent_id_hash"] = agent_id_hash
    if model is not None:
        llm_filter["model"] = model
    if status is not None:
        llm_filter["status"] = status
    if trace_id is not None:
        llm_filter["trace_id"] = trace_id
    if thought_id is not None:
        llm_filter["thought_id"] = thought_id
    try:
        page_json = engine.list_llm_calls(json.dumps(llm_filter), cursor, min(limit, 1000))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_llm_calls failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(page_json)


@router.get("/llm-costs")
async def aggregate_llm_costs(
    hours: float = 168.0,
    agent_id_hash: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Cost rollup by model / agent / domain + window totals via §D
    ``aggregate_llm_costs``."""
    engine = _engine_or_503()
    cost_filter: dict[str, Any] = {
        "time_window": json.loads(_hours_to_window_json(hours)),
    }
    if agent_id_hash is not None:
        cost_filter["agent_id_hash"] = agent_id_hash
    if model is not None:
        cost_filter["model"] = model
    try:
        agg_json = engine.aggregate_llm_costs(json.dumps(cost_filter))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("aggregate_llm_costs failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(agg_json)


# ── §G — corpus shape ───────────────────────────────────────────────

@router.get("/corpus-shape")
async def corpus_shape(
    hours: float = 168.0,
    agent_id_hash: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """6-breakdown corpus rollup (task_class / qa_language /
    qa_question_num / agent_name / agent_version / primary_model /
    deployment_region) over the window. §G ``corpus_shape``."""
    engine = _engine_or_503()
    shape_filter: dict[str, Any] = {
        "time_window": json.loads(_hours_to_window_json(hours)),
    }
    if agent_id_hash is not None:
        shape_filter["agent_id_hash"] = agent_id_hash
    if agent_name is not None:
        shape_filter["agent_name"] = agent_name
    try:
        shape_json = engine.corpus_shape(json.dumps(shape_filter))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("corpus_shape failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(shape_json)


# ── §H — privacy / scrub observability ──────────────────────────────

@router.get("/scrub-stats")
async def aggregate_scrub_stats(hours: float = 168.0) -> dict[str, Any]:
    """Envelopes scrubbed + per-trace-level breakdown over the window.
    §H ``aggregate_scrub_stats``. ``by_entity_type`` +
    ``fields_scrubbed_total`` are wired but gated on the v0.6.0
    post-ingest classification pipeline."""
    engine = _engine_or_503()
    until = datetime.now(UTC)
    since = until - timedelta(hours=hours)
    try:
        agg_json = engine.aggregate_scrub_stats(since.isoformat(), until.isoformat())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("aggregate_scrub_stats failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(agg_json)


# ── §I — federation observability bulk ──────────────────────────────

@router.get("/federation-keys")
async def list_federation_keys(
    cursor: str | None = None,
    limit: int = 100,
    agent_id_hash: str | None = None,
    algorithm: str | None = None,
    revoked: bool | None = None,
    pqc_completed: bool | None = None,
) -> dict[str, Any]:
    """Bulk-list ``federation_keys`` via §I."""
    engine = _engine_or_503()
    key_filter: dict[str, Any] = {}
    if agent_id_hash is not None:
        key_filter["agent_id_hash"] = agent_id_hash
    if algorithm is not None:
        key_filter["algorithm"] = algorithm
    if revoked is not None:
        key_filter["revoked"] = revoked
    if pqc_completed is not None:
        key_filter["pqc_completed"] = pqc_completed
    try:
        page_json = engine.list_federation_keys(
            json.dumps(key_filter), cursor, min(limit, 1000),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_federation_keys failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(page_json)


@router.get("/attestations")
async def list_attestations(
    cursor: str | None = None,
    limit: int = 100,
    attesting_key_id: str | None = None,
    attested_key_id: str | None = None,
    attestation_type: str | None = None,
) -> dict[str, Any]:
    """Bulk-list ``federation_attestations`` via §I."""
    engine = _engine_or_503()
    att_filter: dict[str, Any] = {}
    if attesting_key_id is not None:
        att_filter["attesting_key_id"] = attesting_key_id
    if attested_key_id is not None:
        att_filter["attested_key_id"] = attested_key_id
    if attestation_type is not None:
        att_filter["attestation_type"] = attestation_type
    try:
        page_json = engine.list_attestations(
            json.dumps(att_filter), cursor, min(limit, 1000),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_attestations failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(page_json)


@router.get("/revocations")
async def list_revocations(
    cursor: str | None = None,
    limit: int = 100,
    revoked_key_id: str | None = None,
    revoking_key_id: str | None = None,
    pqc_completed: bool | None = None,
) -> dict[str, Any]:
    """Bulk-list ``federation_revocations`` via §I."""
    engine = _engine_or_503()
    rev_filter: dict[str, Any] = {}
    if revoked_key_id is not None:
        rev_filter["revoked_key_id"] = revoked_key_id
    if revoking_key_id is not None:
        rev_filter["revoking_key_id"] = revoking_key_id
    if pqc_completed is not None:
        rev_filter["pqc_completed"] = pqc_completed
    try:
        page_json = engine.list_revocations(
            json.dumps(rev_filter), cursor, min(limit, 1000),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("list_revocations failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    return json.loads(page_json)


@router.get("/repository/statistics")
async def get_repository_statistics(
    access_level: AccessLevel = AccessLevel.PUBLIC,  # noqa: ARG001 - reserved for future scoping
    domain: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Get aggregate statistics for traces. Available at all access levels."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Default to last 30 days
    if not end_time:
        end_time = datetime.now(UTC)
    if not start_time:
        from datetime import timedelta
        start_time = end_time - timedelta(days=30)

    # Build domain filter
    domain_filter = ""
    params: list[Any] = [start_time, end_time]
    if domain:
        domain_filter = " AND dsdma_domain = $3"
        params.append(domain)

    async with db_pool.acquire() as conn:
        # Base stats
        stats = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*) as trace_count,
                COUNT(DISTINCT agent_id_hash) as agent_count,
                COUNT(DISTINCT dsdma_domain) as domain_count,
                AVG(csdma_plausibility_score) as avg_plausibility,
                AVG(dsdma_domain_alignment) as avg_alignment,
                AVG(idma_k_eff) as avg_k_eff,
                AVG(CASE WHEN conscience_passed THEN 1.0 ELSE 0.0 END) as conscience_pass_rate,
                AVG(CASE WHEN action_was_overridden THEN 1.0 ELSE 0.0 END) as override_rate,
                AVG(CASE WHEN idma_fragility_flag THEN 1.0 ELSE 0.0 END) as fragility_rate
            FROM cirislens.accord_traces
            WHERE timestamp >= $1 AND timestamp <= $2{domain_filter}
            """,
            *params,
        )

        # Action distribution
        actions = await conn.fetch(
            f"""
            SELECT selected_action, COUNT(*) as count
            FROM cirislens.accord_traces
            WHERE timestamp >= $1 AND timestamp <= $2{domain_filter}
            AND selected_action IS NOT NULL
            GROUP BY selected_action
            """,
            *params,
        )

        total_actions = sum(r["count"] for r in actions)
        action_dist = {
            r["selected_action"]: r["count"] / total_actions if total_actions > 0 else 0
            for r in actions
        }

        # By domain (only if not filtering by specific domain)
        by_domain_results = []
        if not domain:
            by_domain = await conn.fetch(
                """
                SELECT
                    dsdma_domain as domain,
                    COUNT(*) as traces,
                    AVG(csdma_plausibility_score) as avg_plausibility,
                    AVG(dsdma_domain_alignment) as avg_alignment
                FROM cirislens.accord_traces
                WHERE timestamp >= $1 AND timestamp <= $2
                AND dsdma_domain IS NOT NULL
                GROUP BY dsdma_domain
                ORDER BY traces DESC
                """,
                start_time,
                end_time,
            )
            by_domain_results = [
                {
                    "domain": r["domain"],
                    "traces": r["traces"],
                    "avg_plausibility": float(r["avg_plausibility"] or 0),
                    "avg_alignment": float(r["avg_alignment"] or 0),
                }
                for r in by_domain
            ]

        return {
            "period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
            "totals": {
                "traces": stats["trace_count"],
                "agents": stats["agent_count"],
                "domains": stats["domain_count"],
            },
            "scores": {
                "csdma_plausibility": {"mean": float(stats["avg_plausibility"] or 0)},
                "dsdma_alignment": {"mean": float(stats["avg_alignment"] or 0)},
                "idma_k_eff": {"mean": float(stats["avg_k_eff"] or 0)},
            },
            "conscience": {
                "pass_rate": float(stats["conscience_pass_rate"] or 0),
                "override_rate": float(stats["override_rate"] or 0),
            },
            "actions": {
                "distribution": action_dist,
            },
            "fragility": {
                "fragile_trace_rate": float(stats["fragility_rate"] or 0),
            },
            "by_domain": by_domain_results,
        }


@router.put("/repository/traces/{trace_id}/public-sample")
async def set_trace_public_sample(
    trace_id: str,
    request: PublicSampleRequest,
    access_level: AccessLevel = AccessLevel.FULL,
    user_id: str = "admin",
) -> dict[str, Any]:
    """Mark a trace as a public sample. Full access only."""
    if access_level != AccessLevel.FULL:
        raise HTTPException(status_code=403, detail="Full access required")

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE cirislens.accord_traces
            SET public_sample = $1,
                access_updated_at = NOW(),
                access_updated_by = $2
            WHERE trace_id = $3
            """,
            request.public_sample,
            user_id,
            trace_id,
        )

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Trace not found")

        # Path parameter trace_id is validated by FastAPI and safe to log
        logger.info(  # NOSONAR - trace_id is a validated path param, not arbitrary user input
            "Trace %s public_sample updated by %s",
            trace_id,
            user_id,
        )

        return {
            "trace_id": trace_id,
            "public_sample": request.public_sample,
            "updated_at": datetime.now(UTC).isoformat(),
        }


@router.put("/repository/traces/{trace_id}/partner-access")
async def set_trace_partner_access(
    trace_id: str,
    request: PartnerAccessRequest,
    access_level: AccessLevel = AccessLevel.FULL,
    user_id: str = "admin",
) -> dict[str, Any]:
    """Modify partner access for a trace. Full access only."""
    if access_level != AccessLevel.FULL:
        raise HTTPException(status_code=403, detail="Full access required")

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        # Get current access
        current = await conn.fetchval(
            "SELECT partner_access FROM cirislens.accord_traces WHERE trace_id = $1",
            trace_id,
        )

        if current is None:
            raise HTTPException(status_code=404, detail="Trace not found")

        current_set = set(current or [])

        if request.action == "add":
            new_access = list(current_set | set(request.partner_ids))
        elif request.action == "remove":
            new_access = list(current_set - set(request.partner_ids))
        elif request.action == "set":
            new_access = request.partner_ids
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        await conn.execute(
            """
            UPDATE cirislens.accord_traces
            SET partner_access = $1,
                access_updated_at = NOW(),
                access_updated_by = $2
            WHERE trace_id = $3
            """,
            new_access,
            user_id,
            trace_id,
        )

        logger.info(
            "Trace %s partner_access updated by %s: %s %s",
            trace_id,
            user_id,
            request.action,
            request.partner_ids,
        )

        return {
            "trace_id": trace_id,
            "partner_access": new_access,
            "updated_at": datetime.now(UTC).isoformat(),
        }


# =============================================================================
# Coherence Ratchet Detection API
# Reference: FSD/coherence_ratchet_detection.md
# =============================================================================


class CoherenceRatchetAlertResponse(BaseModel):
    """Response model for Coherence Ratchet alerts."""

    alert_id: str
    alert_type: str
    severity: str
    detection_mechanism: str
    agent_id_hash: str | None
    domain: str | None
    metric: str
    value: float | None
    baseline: float | None
    deviation: str | None
    timestamp: datetime
    evidence_traces: list[str]
    recommended_action: str | None
    acknowledged: bool
    resolved: bool


class RunDetectionResponse(BaseModel):
    """Response for running detection manually."""

    alerts_found: int
    alerts: list[dict[str, Any]]


class AcknowledgeAlertRequest(BaseModel):
    """Request to acknowledge an alert."""

    acknowledged_by: str


class ResolveAlertRequest(BaseModel):
    """Request to resolve an alert."""

    resolved_by: str
    resolution_notes: str | None = None


# Singleton scheduler instance (initialized in main.py)
_scheduler: Any = None


def get_scheduler() -> Any:
    """Get the scheduler instance."""
    return _scheduler


def set_scheduler(scheduler: Any) -> None:
    """Set the scheduler instance (called from main.py)."""
    global _scheduler
    _scheduler = scheduler


@router.get("/coherence-ratchet/alerts")
async def list_coherence_ratchet_alerts(
    hours: int = 24,
    severity: str | None = None,
    detection_mechanism: str | None = None,
    unacknowledged_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """
    List Coherence Ratchet anomaly alerts.

    Args:
        hours: How many hours back to look (default 24)
        severity: Filter by severity (warning, critical)
        detection_mechanism: Filter by detection type
        unacknowledged_only: Only show unacknowledged alerts
        limit: Maximum alerts to return (default 100, max 1000)
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = """
        SELECT alert_id, alert_type, severity, detection_mechanism,
               agent_id_hash, domain, metric, value, baseline, deviation,
               timestamp, evidence_traces, recommended_action,
               acknowledged, acknowledged_at, acknowledged_by,
               resolved, resolved_at, resolved_by, resolution_notes
        FROM cirislens.coherence_ratchet_alerts
        WHERE timestamp > NOW() - $1::interval
    """
    params: list[Any] = [f"{hours} hours"]
    param_idx = 2

    if severity:
        query += f" AND severity = ${param_idx}"
        params.append(severity)
        param_idx += 1

    if detection_mechanism:
        query += f" AND detection_mechanism = ${param_idx}"
        params.append(detection_mechanism)
        param_idx += 1

    if unacknowledged_only:
        query += " AND acknowledged = FALSE"

    query += f" ORDER BY timestamp DESC LIMIT ${param_idx}"
    params.append(min(limit, 1000))

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

        alerts = [
            {
                "alert_id": str(row["alert_id"]),
                "alert_type": row["alert_type"],
                "severity": row["severity"],
                "detection_mechanism": row["detection_mechanism"],
                "agent_id_hash": row["agent_id_hash"],
                "domain": row["domain"],
                "metric": row["metric"],
                "value": float(row["value"]) if row["value"] else None,
                "baseline": float(row["baseline"]) if row["baseline"] else None,
                "deviation": row["deviation"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                "evidence_traces": row["evidence_traces"] or [],
                "recommended_action": row["recommended_action"],
                "acknowledged": row["acknowledged"],
                "acknowledged_at": row["acknowledged_at"].isoformat() if row["acknowledged_at"] else None,
                "acknowledged_by": row["acknowledged_by"],
                "resolved": row["resolved"],
                "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                "resolved_by": row["resolved_by"],
                "resolution_notes": row["resolution_notes"],
            }
            for row in rows
        ]

        return {"alerts": alerts, "count": len(alerts)}


@router.post("/coherence-ratchet/run")
async def run_coherence_ratchet_detection() -> RunDetectionResponse:
    """
    Manually trigger all Coherence Ratchet detection mechanisms.

    This runs all Phase 1 detections immediately and returns any anomalies found.
    """
    scheduler = get_scheduler()
    if scheduler is None:
        # Fall back to direct analyzer if scheduler not initialized.
        # Prefer the persist Engine path (federation-uniform §F
        # primitives); fall back to db_pool legacy SQL.
        try:
            from api.analysis.coherence_ratchet import CoherenceRatchetAnalyzer
        except ImportError:
            from analysis.coherence_ratchet import CoherenceRatchetAnalyzer

        engine = persist_engine.get_engine()
        db_pool = get_db_pool()
        if engine is None and db_pool is None:
            raise HTTPException(status_code=503, detail="No analysis backend available")

        analyzer = CoherenceRatchetAnalyzer(db_pool=db_pool, engine=engine)
        alerts = await analyzer.run_all_detections()
    else:
        alerts = await scheduler.run_all_now()

    return RunDetectionResponse(
        alerts_found=len(alerts),
        alerts=[a.to_dict() for a in alerts],
    )


@router.put("/coherence-ratchet/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    request: AcknowledgeAlertRequest,
) -> dict[str, Any]:
    """
    Acknowledge a Coherence Ratchet alert.

    Acknowledging indicates that a human has reviewed the alert.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = """
        UPDATE cirislens.coherence_ratchet_alerts
        SET acknowledged = TRUE,
            acknowledged_at = NOW(),
            acknowledged_by = $2
        WHERE alert_id = $1::uuid
        RETURNING alert_id;
    """

    async with db_pool.acquire() as conn:
        result = await conn.fetchval(query, alert_id, request.acknowledged_by)
        if result is None:
            raise HTTPException(status_code=404, detail="Alert not found")

        return {"status": "acknowledged", "alert_id": alert_id}


@router.put("/coherence-ratchet/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    request: ResolveAlertRequest,
) -> dict[str, Any]:
    """
    Resolve a Coherence Ratchet alert.

    Resolution indicates the anomaly has been investigated and addressed.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = """
        UPDATE cirislens.coherence_ratchet_alerts
        SET resolved = TRUE,
            resolved_at = NOW(),
            resolved_by = $2,
            resolution_notes = $3
        WHERE alert_id = $1::uuid
        RETURNING alert_id;
    """

    async with db_pool.acquire() as conn:
        result = await conn.fetchval(
            query, alert_id, request.resolved_by, request.resolution_notes
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Alert not found")

        return {"status": "resolved", "alert_id": alert_id}


@router.get("/coherence-ratchet/stats")
async def get_coherence_ratchet_stats(hours: int = 168) -> dict[str, Any]:
    """
    Get Coherence Ratchet detection statistics.

    Args:
        hours: Time window to analyze (default 168 = 7 days)
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    query = """
        SELECT
            COUNT(*) as total_alerts,
            COUNT(*) FILTER (WHERE severity = 'critical') as critical_alerts,
            COUNT(*) FILTER (WHERE severity = 'warning') as warning_alerts,
            COUNT(*) FILTER (WHERE acknowledged = FALSE) as unacknowledged_alerts,
            COUNT(*) FILTER (WHERE resolved = TRUE) as resolved_alerts,
            COUNT(DISTINCT agent_id_hash) as affected_agents,
            detection_mechanism,
            COUNT(*) as mechanism_count
        FROM cirislens.coherence_ratchet_alerts
        WHERE timestamp > NOW() - $1::interval
        GROUP BY detection_mechanism
        ORDER BY mechanism_count DESC;
    """

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, f"{hours} hours")

        if not rows:
            return {
                "total_alerts": 0,
                "critical_alerts": 0,
                "warning_alerts": 0,
                "unacknowledged_alerts": 0,
                "resolved_alerts": 0,
                "affected_agents": 0,
                "by_mechanism": {},
                "hours_analyzed": hours,
            }

        # Aggregate totals from first row (all have same totals due to GROUP BY)
        first = rows[0]
        by_mechanism = {row["detection_mechanism"]: row["mechanism_count"] for row in rows}

        return {
            "total_alerts": first["total_alerts"],
            "critical_alerts": first["critical_alerts"],
            "warning_alerts": first["warning_alerts"],
            "unacknowledged_alerts": first["unacknowledged_alerts"],
            "resolved_alerts": first["resolved_alerts"],
            "affected_agents": first["affected_agents"],
            "by_mechanism": by_mechanism,
            "hours_analyzed": hours,
        }
