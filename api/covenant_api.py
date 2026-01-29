"""
CIRIS Covenant 1.0b API Endpoints

Provides API endpoints for:
- Wisdom-Based Deferral (WBD) events
- PDMA (Principled Decision-Making Algorithm) events
- Creator Ledger entries
- Sunset Protocol tracking
- Covenant compliance status

Reference: covenant_1.0b.txt Sections I-VIII
"""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import asyncpg

try:
    from pii_scrubber import get_scrubber, scrub_dict_recursive
except ImportError:
    from api.pii_scrubber import get_scrubber, scrub_dict_recursive

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
    """Get the database pool from main module. Avoids circular import."""
    import main

    return main.db_pool

# Create router for Covenant endpoints
router = APIRouter(prefix="/api/v1/covenant", tags=["covenant"])


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
    signature_key_id: str  # e.g., "wa-2025-06-14-ROOT00"


class CovenantTraceEvent(BaseModel):
    """Wrapper for a trace event."""

    event_type: str = "complete_trace"
    trace: CovenantTrace


class CorrelationMetadata(BaseModel):
    """Optional metadata for Early Warning System correlation analysis."""

    deployment_region: str | None = None  # na, eu, uk, apac, latam, mena, africa, oceania
    deployment_type: str | None = None  # personal, business, research, nonprofit
    agent_role: str | None = None  # assistant, customer_support, content, coding, etc.
    agent_template: str | None = None  # CIRIS template name if using standard template


class CovenantEventsRequest(BaseModel):
    """Batch of covenant trace events."""

    events: list[CovenantTraceEvent]
    batch_timestamp: datetime
    consent_timestamp: datetime
    trace_level: str = "generic"  # generic, detailed, full_traces
    correlation_metadata: CorrelationMetadata | None = None


class CovenantEventsResponse(BaseModel):
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
                FROM cirislens.covenant_public_keys
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
    trace: CovenantTrace, public_keys: dict[str, bytes]
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
        message = json.dumps(
            [c.model_dump() for c in trace.components], sort_keys=True
        ).encode()

        # Debug logging for signature verification
        logger.debug(
            "Verifying trace %s: sig_len=%d, msg_len=%d, key_id=%s",
            trace.trace_id, len(signature), len(message), trace.signature_key_id
        )
        logger.debug("Message hash (first 100 chars): %s", message[:100].decode())

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
            selection_confidence, is_recursive, follow_up_thought_id, api_bases_used,
            schema_version,
            idma_result, tsaspdma_result,
            tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
            $51, $52, $53, $54, $55, $56, $57, $58, $59, $60,
            $61, $62, $63, $64, $65, $66
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
    )


def extract_trace_metadata(trace: CovenantTrace, trace_level: str = "generic") -> dict[str, Any]:
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
            # Extract agent name from system_snapshot
            sys_snapshot = data.get("system_snapshot", {})
            agent_identity = sys_snapshot.get("agent_identity", {})
            metadata["agent_name"] = agent_identity.get("agent_id")

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

        elif event_type == "IDMA_RESULT":
            # V1.9.3: IDMA as separate event (not nested in DMA_RESULTS)
            metadata["idma_result"] = data
            # Extract IDMA fields (same as from DMA_RESULTS.idma but from separate event)
            metadata["idma_k_eff"] = data.get("k_eff")
            metadata["idma_correlation_risk"] = data.get("correlation_risk")
            metadata["idma_fragility_flag"] = data.get("fragility_flag")
            metadata["idma_phase"] = data.get("phase")

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

    return metadata


# =============================================================================
# API Endpoint - Covenant Events Receiver
# Reference: FSD/covenant_events_receiver.md
# =============================================================================


@router.post("/events", response_model=CovenantEventsResponse)
async def receive_covenant_events(
    request: CovenantEventsRequest,
) -> dict[str, Any]:
    """
    Receive Ed25519-signed reasoning traces from CIRIS agents.

    This endpoint implements the Coherence Ratchet receiver, accepting
    immutable records of agent decision-making for transparency and
    alignment validation.

    Reference: Covenant Section IV - Ethical Integrity Surveillance
    """
    import base64

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
            is_valid, error = verify_trace_signature(trace, public_keys)

            if not is_valid and public_keys:
                rejected += 1
                rejected_traces.append(trace.trace_id)
                if error:
                    errors.append(f"{trace.trace_id}: {error}")
                continue

            # PII Scrubbing for full_traces level
            # Must happen BEFORE metadata extraction to scrub text fields
            pii_scrubbed = False
            original_content_hash = None
            scrub_timestamp = None
            scrub_signature = None
            scrub_key_id = None

            if request.trace_level == "full_traces":
                scrubber = get_scrubber()

                # Compute hash of original content before any modification
                original_message = json.dumps(
                    [c.model_dump() for c in trace.components], sort_keys=True
                ).encode('utf-8')
                original_content_hash = hashlib.sha256(original_message).hexdigest()

                # Scrub PII from each component's data
                scrubbed_components = []
                for comp in trace.components:
                    comp_dict = comp.model_dump()
                    if "data" in comp_dict:
                        comp_dict["data"] = scrub_dict_recursive(comp_dict["data"])
                    scrubbed_components.append(comp_dict)

                # Re-sign scrubbed content with CIRISLens key
                scrub_timestamp = datetime.now(UTC)
                scrub_key_id = scrubber.scrub_key_id
                if scrubber._signing_key:
                    scrubbed_message = json.dumps(scrubbed_components, sort_keys=True).encode('utf-8')
                    scrub_signature = scrubber._signing_key  # Will be signed in scrubber
                    from pii_scrubber import sign_content
                    scrub_signature = sign_content(scrubbed_message, scrubber._signing_key)

                # Update trace components with scrubbed data
                # We modify the trace object in place for metadata extraction
                for i, comp in enumerate(trace.components):
                    for key, value in scrubbed_components[i].get("data", {}).items():
                        if hasattr(comp, "data") and isinstance(comp.data, dict):
                            comp.data[key] = value

                pii_scrubbed = True
                logger.info(
                    "Scrubbed PII from full_traces %s (hash: %s...)",
                    trace.trace_id, original_content_hash[:16]
                )

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
                        tool_name, tool_parameters, tsaspdma_reasoning, tsaspdma_approved
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
                INSERT INTO cirislens.covenant_public_keys (
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
            FROM cirislens.covenant_public_keys
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
    """
    List covenant traces with RBAC access control.

    Access levels:
    - full: All traces, all fields
    - partner: Own agents + public samples + partner-tagged traces
    - public: Public sample traces only (for ciris.ai/explore-a-trace)

    When group_by_task=true, returns traces grouped by task_id with the
    seed observation from the initial thought (depth=0) included.
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
        FROM cirislens.covenant_traces
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
            f"SELECT COUNT(*) FROM cirislens.covenant_traces WHERE 1=1{scope_sql}"
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
async def get_repository_trace(
    trace_id: str,
    access_level: AccessLevel = AccessLevel.PUBLIC,
    user_id: str = "anonymous",
    agent_scope: str | None = None,
    partner_id: str | None = None,
) -> dict[str, Any]:
    """Get a single trace by ID with access control."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    ctx = TraceAccessContext(
        access_level=access_level,
        user_id=user_id,
        agent_scope=agent_scope.split(",") if agent_scope else [],
        partner_id=partner_id,
    )

    query = """
        SELECT * FROM cirislens.covenant_traces
        WHERE trace_id = $1
    """
    params: list[Any] = [trace_id]
    param_idx = 2

    # Apply access control
    scope_sql, scope_params, _ = build_access_scope_filter(ctx, param_idx)
    query += scope_sql
    params.extend(scope_params)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Trace not found or access denied",
            )

        # Build full trace response (same structure as list)
        trace = dict(row)
        # Convert types for JSON serialization
        for key, val in trace.items():
            if isinstance(val, datetime):
                trace[key] = val.isoformat()
            elif isinstance(val, Decimal):
                trace[key] = float(val)
            elif isinstance(val, UUID):
                trace[key] = str(val)

        return filter_trace_fields(trace, access_level)


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
            FROM cirislens.covenant_traces
            WHERE timestamp >= $1 AND timestamp <= $2{domain_filter}
            """,
            *params,
        )

        # Action distribution
        actions = await conn.fetch(
            f"""
            SELECT selected_action, COUNT(*) as count
            FROM cirislens.covenant_traces
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
                FROM cirislens.covenant_traces
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
            UPDATE cirislens.covenant_traces
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

        logger.info(
            "Trace %s public_sample set to %s by %s: %s",
            trace_id,
            request.public_sample,
            user_id,
            request.reason,
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
            "SELECT partner_access FROM cirislens.covenant_traces WHERE trace_id = $1",
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
            UPDATE cirislens.covenant_traces
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
        # Fall back to direct analyzer if scheduler not initialized
        db_pool = get_db_pool()
        if db_pool is None:
            raise HTTPException(status_code=503, detail="Database not available")

        from api.analysis.coherence_ratchet import CoherenceRatchetAnalyzer

        analyzer = CoherenceRatchetAnalyzer(db_pool)
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
