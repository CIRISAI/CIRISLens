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
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import asyncpg

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
