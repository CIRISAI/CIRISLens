"""
CIRIS Scoring API Endpoints

Provides REST API for CIRIS Capacity Score calculations.

Endpoints:
- GET /api/v1/scoring/capacity/{agent_name} - Score for specific agent
- GET /api/v1/scoring/capacity/fleet - Fleet-wide scores
- GET /api/v1/scoring/factors/{agent_name} - Detailed factor breakdown
- GET /api/v1/scoring/alerts - Agents below threshold
- GET /api/v1/scoring/history/{agent_name} - Score history (placeholder)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from ciris_scoring import (
    PARAMS,
    calculate_ciris_score,
    get_alerts,
    get_fleet_scores,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["scoring"])


def get_db_pool() -> Any:
    """Get the database pool from main module. Avoids circular import."""
    import main  # noqa: PLC0415

    return main.db_pool


# ============================================================================
# API Endpoints
# ============================================================================

# NOTE: Fleet endpoint MUST come before parameterized endpoint
# to avoid FastAPI matching "fleet" as an agent_name

@router.get("/capacity/fleet")
async def get_fleet_score(
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get CIRIS Capacity Scores for all agents.

    Args:
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        List of scores sorted by composite score (descending)
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            scores = await get_fleet_scores(conn, window_days)

            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "window_days": window_days,
                "agent_count": len(scores),
                "agents": [s.to_dict() for s in scores],
                "summary": {
                    "high_capacity": sum(1 for s in scores if s.category == "high_capacity"),
                    "healthy": sum(1 for s in scores if s.category == "healthy"),
                    "moderate": sum(1 for s in scores if s.category == "moderate"),
                    "high_fragility": sum(1 for s in scores if s.category == "high_fragility"),
                },
            }

    except Exception as e:
        logger.exception("Error calculating fleet scores")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/capacity/{agent_name}")
async def get_agent_score(
    agent_name: str,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get CIRIS Capacity Score for a specific agent.

    Args:
        agent_name: Name of the agent
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        Complete CIRIS score with all factors
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            score = await calculate_ciris_score(conn, agent_name, window_days)

            if score.total_traces == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"No traces found for agent '{agent_name}' in the last {window_days} days",
                )

            return score.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error calculating score for %s", agent_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/factors/{agent_name}")
async def get_agent_factors(
    agent_name: str,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get detailed factor breakdown for an agent.

    Includes all component values for each of the 5 factors:
    - C: Core Identity
    - I_int: Integrity
    - R: Resilience
    - I_inc: Incompleteness Awareness
    - S: Sustained Coherence

    Args:
        agent_name: Name of the agent
        window_days: Scoring window in days (1-90, default: 7)
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            score = await calculate_ciris_score(conn, agent_name, window_days)

            if score.total_traces == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"No traces found for agent '{agent_name}' in the last {window_days} days",
                )

        return {
            "agent_name": agent_name,
            "composite_score": round(score.composite_score, 4),
            "category": score.category,
            "factors": {
                "C": {
                    "name": "Core Identity",
                    "formula": "C = exp(-λ·D_identity) · exp(-μ·K_contradiction)",
                    "score": round(score.C.score, 4),
                    "components": {k: round(v, 4) for k, v in score.C.components.items()},
                    "trace_count": score.C.trace_count,
                    "confidence": score.C.confidence,
                    "description": "Measures identity stability and policy consistency",
                },
                "I_int": {
                    "name": "Integrity",
                    "formula": "I_int = I_chain · I_coverage · I_replay",
                    "score": round(score.I_int.score, 4),
                    "components": {k: round(v, 4) if isinstance(v, float) else v for k, v in score.I_int.components.items()},
                    "trace_count": score.I_int.trace_count,
                    "confidence": score.I_int.confidence,
                    "description": "Measures hash chain integrity and field completeness",
                },
                "R": {
                    "name": "Resilience",
                    "formula": "R = sigmoid((1-delta_drift) * 1/(1+MTTR) * (1-rho_regression))",
                    "score": round(score.R.score, 4),
                    "components": {k: round(v, 4) for k, v in score.R.components.items()},
                    "trace_count": score.R.trace_count,
                    "confidence": score.R.confidence,
                    "notes": score.R.notes,
                    "description": "Measures score stability and recovery capability",
                },
                "I_inc": {
                    "name": "Incompleteness Awareness",
                    "formula": "I_inc = (1-ECE) · Q_deferral · (1-U_unsafe)",
                    "score": round(score.I_inc.score, 4),
                    "components": {k: round(v, 4) if isinstance(v, float) else v for k, v in score.I_inc.components.items()},
                    "trace_count": score.I_inc.trace_count,
                    "confidence": score.I_inc.confidence,
                    "notes": score.I_inc.notes,
                    "description": "Measures calibration and uncertainty handling",
                },
                "S": {
                    "name": "Sustained Coherence",
                    "formula": "S = S_base · (1 + w_pm·P_positive) · (1 + w_ef·P_ethical)",
                    "score": round(score.S.score, 4),
                    "components": {k: round(v, 4) if isinstance(v, float) else v for k, v in score.S.components.items()},
                    "trace_count": score.S.trace_count,
                    "confidence": score.S.confidence,
                    "description": "Measures coherence over time with positive engagement",
                },
            },
            "metadata": {
                "window_start": score.window_start.isoformat(),
                "window_end": score.window_end.isoformat(),
                "total_traces": score.total_traces,
                "non_exempt_traces": score.non_exempt_traces,
                "non_exempt_actions": ["SPEAK", "TOOL", "MEMORIZE", "FORGET"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting factors for %s", agent_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/alerts")
async def get_scoring_alerts(
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = 0.3,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get agents with scores below threshold (high fragility).

    Args:
        threshold: Score threshold (default: 0.3 = high fragility)
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        List of agents requiring attention
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            alerts = await get_alerts(conn, threshold, window_days)

            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "threshold": threshold,
                "window_days": window_days,
                "alert_count": len(alerts),
                "agents": [
                    {
                        "agent_name": s.agent_name,
                        "composite_score": round(s.composite_score, 4),
                        "category": s.category,
                        "fragility_index": round(s.fragility_index, 4),
                        "weakest_factor": min(
                            [("C", s.C.score), ("I_int", s.I_int.score), ("R", s.R.score),
                             ("I_inc", s.I_inc.score), ("S", s.S.score)],
                            key=lambda x: x[1]
                        )[0],
                        "non_exempt_traces": s.non_exempt_traces,
                    }
                    for s in alerts
                ],
            }

    except Exception as e:
        logger.exception("Error getting scoring alerts")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/history/{agent_name}")
async def get_agent_history(
    agent_name: str,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    interval: Annotated[str, Query()] = "daily",
):
    """
    Get score history for an agent over time.

    Args:
        agent_name: Name of the agent
        days: History period in days (1-90, default: 30)
        interval: Aggregation interval ("hourly" or "daily")

    Returns:
        Time series of scores

    Note: This is a placeholder - full implementation requires pre-computed scores.
    """
    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # For now, calculate current score and return placeholder history
    try:
        async with db_pool.acquire() as conn:
            current = await calculate_ciris_score(conn, agent_name, 7)

            if current.total_traces == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"No traces found for agent '{agent_name}'",
                )

            return {
                "agent_name": agent_name,
                "period_days": days,
                "interval": interval,
                "current_score": round(current.composite_score, 4),
                "current_category": current.category,
                "history": [],  # Placeholder - requires score persistence
                "note": "Historical scores require pre-computation (not yet implemented)",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting history for %s", agent_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/parameters")
async def get_scoring_parameters():
    """
    Get the current scoring parameters.

    Returns configuration values used in score calculations.
    """
    return {
        "parameters": PARAMS,
        "non_exempt_actions": ["SPEAK", "TOOL", "MEMORIZE", "FORGET"],
        "exempt_actions": ["TASK_COMPLETE", "RECALL", "OBSERVE", "DEFER", "REJECT", "PONDER"],
        "categories": {
            "high_fragility": "< 0.3 - Immediate intervention required",
            "moderate": "0.3 - 0.6 - Low-stakes tasks with human review",
            "healthy": "0.6 - 0.85 - Standard autonomous operation",
            "high_capacity": ">= 0.85 - Eligible for expanded autonomy",
        },
    }
