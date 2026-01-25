"""
CIRIS Scoring API Endpoints

Provides REST API for CIRIS Capacity Score calculations.

Public Endpoints (no auth, rate limited, cached):
- GET /api/v1/scoring/capacity/fleet - Fleet-wide scores
- GET /api/v1/scoring/capacity/{agent_name} - Score for specific agent
- GET /api/v1/scoring/factors/{agent_name} - Detailed factor breakdown
- GET /api/v1/scoring/alerts - Agents below threshold
- GET /api/v1/scoring/parameters - Scoring configuration

Rate Limiting: 60 requests/minute per IP
Cache TTL: 5 minutes (scores change slowly)
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from ciris_scoring import (
    PARAMS,
    calculate_ciris_score,
    get_alerts,
    get_fleet_scores,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["scoring"])


# ============================================================================
# Caching Layer
# ============================================================================

@dataclass
class CacheEntry:
    """Cache entry with TTL."""

    data: Any
    expires_at: float


class TTLCache:
    """Thread-safe in-memory cache with TTL."""

    def __init__(self, default_ttl: int = 300):
        """Initialize cache with default TTL in seconds."""
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry.expires_at:
                del self._cache[key]
                return None
            return entry.data

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set value in cache with TTL."""
        ttl = ttl or self._default_ttl
        with self._lock:
            self._cache[key] = CacheEntry(
                data=value,
                expires_at=time.time() + ttl,
            )

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        with self._lock:
            now = time.time()
            valid = sum(1 for e in self._cache.values() if e.expires_at > now)
            return {
                "total_entries": len(self._cache),
                "valid_entries": valid,
                "expired_entries": len(self._cache) - valid,
            }


# Global cache instance - 5 minute TTL
score_cache = TTLCache(default_ttl=300)


# ============================================================================
# Rate Limiting
# ============================================================================

class RateLimiter:
    """IP-based sliding window rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self._requests_per_minute = requests_per_minute
        self._window_size = 60  # 1 minute window
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier from request."""
        # Check X-Forwarded-For for proxied requests
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take first IP in chain (original client)
            return forwarded.split(",")[0].strip()
        # Fall back to direct client IP
        return request.client.host if request.client else "unknown"

    def is_allowed(self, request: Request) -> tuple[bool, dict[str, Any]]:
        """Check if request is allowed under rate limit."""
        client_id = self._get_client_id(request)
        now = time.time()
        window_start = now - self._window_size

        with self._lock:
            # Clean old requests outside window
            self._requests[client_id] = [
                t for t in self._requests[client_id] if t > window_start
            ]

            current_count = len(self._requests[client_id])
            remaining = max(0, self._requests_per_minute - current_count)

            if current_count >= self._requests_per_minute:
                # Calculate retry-after
                oldest = min(self._requests[client_id]) if self._requests[client_id] else now
                retry_after = int(oldest + self._window_size - now) + 1
                return False, {
                    "limit": self._requests_per_minute,
                    "remaining": 0,
                    "retry_after": retry_after,
                }

            # Record this request
            self._requests[client_id].append(now)

            return True, {
                "limit": self._requests_per_minute,
                "remaining": remaining - 1,
                "retry_after": 0,
            }


# Global rate limiter - 60 requests per minute
rate_limiter = RateLimiter(requests_per_minute=60)


def check_rate_limit(request: Request) -> None:
    """Check rate limit and raise 429 if exceeded."""
    allowed, info = rate_limiter.is_allowed(request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": info["limit"],
                "retry_after": info["retry_after"],
            },
            headers={
                "X-RateLimit-Limit": str(info["limit"]),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(info["retry_after"]),
            },
        )


# ============================================================================
# Helper Functions
# ============================================================================

def get_db_pool() -> Any:
    """Get the database pool from main module. Avoids circular import."""
    import main  # noqa: PLC0415

    return main.db_pool


def cache_key(*args: Any) -> str:
    """Generate cache key from arguments."""
    key_str = ":".join(str(a) for a in args)
    return hashlib.md5(key_str.encode()).hexdigest()  # noqa: S324


# ============================================================================
# Public API Endpoints (Rate Limited + Cached)
# ============================================================================

# NOTE: Fleet endpoint MUST come before parameterized endpoint
# to avoid FastAPI matching "fleet" as an agent_name

@router.get("/capacity/fleet")
async def get_fleet_score(
    request: Request,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get CIRIS Capacity Scores for all agents.

    Public endpoint with rate limiting (60/min) and caching (5 min TTL).

    Args:
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        List of scores sorted by composite score (descending)
    """
    check_rate_limit(request)

    # Check cache
    key = cache_key("fleet", window_days)
    cached = score_cache.get(key)
    if cached is not None:
        logger.debug("Cache hit for fleet scores (window=%d)", window_days)
        return cached

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            scores = await get_fleet_scores(conn, window_days)

            result = {
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
                "cache": {
                    "cached": False,
                    "ttl_seconds": 300,
                },
            }

            # Cache the result
            score_cache.set(key, result)
            logger.info("Cached fleet scores for window=%d (%d agents)", window_days, len(scores))

            return result

    except Exception as e:
        logger.exception("Error calculating fleet scores")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/capacity/{agent_name}")
async def get_agent_score(
    request: Request,
    agent_name: str,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get CIRIS Capacity Score for a specific agent.

    Public endpoint with rate limiting (60/min) and caching (5 min TTL).

    Args:
        agent_name: Name of the agent
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        Complete CIRIS score with all factors
    """
    check_rate_limit(request)

    # Check cache
    key = cache_key("agent", agent_name, window_days)
    cached = score_cache.get(key)
    if cached is not None:
        logger.debug("Cache hit for agent %s (window=%d)", agent_name, window_days)
        return cached

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

            result = score.to_dict()
            result["cache"] = {"cached": False, "ttl_seconds": 300}

            # Cache the result
            score_cache.set(key, result)

            return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error calculating score for %s", agent_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/factors/{agent_name}")
async def get_agent_factors(
    request: Request,
    agent_name: str,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get detailed factor breakdown for an agent.

    Public endpoint with rate limiting (60/min) and caching (5 min TTL).

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
    check_rate_limit(request)

    # Check cache
    key = cache_key("factors", agent_name, window_days)
    cached = score_cache.get(key)
    if cached is not None:
        logger.debug("Cache hit for factors %s (window=%d)", agent_name, window_days)
        return cached

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

        result = {
            "agent_name": agent_name,
            "composite_score": round(score.composite_score, 4),
            "category": score.category,
            "factors": {
                "C": {
                    "name": "Core Identity",
                    "formula": "C = exp(-lambda*D_identity) * exp(-mu*K_contradiction)",
                    "score": round(score.C.score, 4),
                    "components": {k: round(v, 4) for k, v in score.C.components.items()},
                    "trace_count": score.C.trace_count,
                    "confidence": score.C.confidence,
                    "description": "Measures identity stability and policy consistency",
                },
                "I_int": {
                    "name": "Integrity",
                    "formula": "I_int = I_chain * I_coverage * I_replay",
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
                    "formula": "I_inc = (1-ECE) * Q_deferral * (1-U_unsafe)",
                    "score": round(score.I_inc.score, 4),
                    "components": {k: round(v, 4) if isinstance(v, float) else v for k, v in score.I_inc.components.items()},
                    "trace_count": score.I_inc.trace_count,
                    "confidence": score.I_inc.confidence,
                    "notes": score.I_inc.notes,
                    "description": "Measures calibration and uncertainty handling",
                },
                "S": {
                    "name": "Sustained Coherence",
                    "formula": "S = S_base * (1 + w_pm*P_positive) * (1 + w_ef*P_ethical)",
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
            "cache": {"cached": False, "ttl_seconds": 300},
        }

        # Cache the result
        score_cache.set(key, result)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting factors for %s", agent_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/alerts")
async def get_scoring_alerts(
    request: Request,
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = 0.3,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get agents with scores below threshold (high fragility).

    Public endpoint with rate limiting (60/min) and caching (5 min TTL).

    Args:
        threshold: Score threshold (default: 0.3 = high fragility)
        window_days: Scoring window in days (1-90, default: 7)

    Returns:
        List of agents requiring attention
    """
    check_rate_limit(request)

    # Check cache
    key = cache_key("alerts", threshold, window_days)
    cached = score_cache.get(key)
    if cached is not None:
        logger.debug("Cache hit for alerts (threshold=%.2f, window=%d)", threshold, window_days)
        return cached

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            alerts = await get_alerts(conn, threshold, window_days)

            result = {
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
                "cache": {"cached": False, "ttl_seconds": 300},
            }

            # Cache the result
            score_cache.set(key, result)

            return result

    except Exception as e:
        logger.exception("Error getting scoring alerts")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/history/{agent_name}")
async def get_agent_history(
    request: Request,
    agent_name: str,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    interval: Annotated[str, Query()] = "daily",
):
    """
    Get score history for an agent over time.

    Public endpoint with rate limiting (60/min).

    Args:
        agent_name: Name of the agent
        days: History period in days (1-90, default: 30)
        interval: Aggregation interval ("hourly" or "daily")

    Returns:
        Time series of scores

    Note: This is a placeholder - full implementation requires pre-computed scores.
    """
    check_rate_limit(request)

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
async def get_scoring_parameters(request: Request):
    """
    Get the current scoring parameters.

    Public endpoint with rate limiting (60/min).

    Returns configuration values used in score calculations.
    """
    check_rate_limit(request)

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
        "rate_limit": {
            "requests_per_minute": 60,
            "cache_ttl_seconds": 300,
        },
    }


@router.get("/cache/stats")
async def get_cache_stats(request: Request):
    """
    Get cache statistics.

    Public endpoint for monitoring cache performance.
    """
    check_rate_limit(request)

    return {
        "cache": score_cache.stats(),
        "rate_limit": {
            "requests_per_minute": 60,
        },
    }


@router.post("/cache/clear")
async def clear_cache(request: Request):
    """
    Clear the score cache.

    This endpoint requires the request to come from localhost or have admin auth.
    """
    # Only allow from localhost for safety
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For", "")

    if client_host not in ("127.0.0.1", "localhost", "::1") and "127.0.0.1" not in forwarded:
        raise HTTPException(status_code=403, detail="Cache clear only allowed from localhost")

    score_cache.clear()
    logger.info("Score cache cleared by request from %s", client_host)

    return {"status": "cleared", "timestamp": datetime.now(UTC).isoformat()}
