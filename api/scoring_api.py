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

import asyncio
import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

import persist_engine
from ciris_scoring import (
    PARAMS,
    calculate_ciris_score,
    calculate_ciris_score_via_persist,
    calculate_fleet_scores_via_persist,
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
    """Thread-safe in-memory cache with TTL.

    Stale-while-revalidate: `get` enforces the TTL, but `get_stale`
    returns the entry regardless of expiry. The fleet handler uses
    `get_stale` as a fallback so the first request after a cache miss
    sees the previous result (~0.1s) instead of blocking on the ~65s
    cold compute. A background warmer keeps the cache perpetually
    fresh in steady state — see CIRISLens#17 follow-on perf work.
    """

    def __init__(self, default_ttl: int = 900):
        """Initialize cache with default TTL in seconds. Default 900s
        (15min); the background warmer recomputes every 240s, so the
        cache never expires during a user request in steady state."""
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
                # Don't delete — `get_stale` may want it. Expired
                # entries are reaped by `set` overwriting them.
                return None
            return entry.data

    def get_stale(self, key: str) -> Any | None:
        """Return the cached entry even if expired. Used by SWR
        fallback so the user-facing path serves stale instead of
        blocking on a cold recompute."""
        with self._lock:
            entry = self._cache.get(key)
            return entry.data if entry is not None else None

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


# Global cache instance. TTL=900s (15min) pairs with the
# WARMER_INTERVAL=240s (4min) below so a healthy warmer keeps the
# cache continuously fresh and the user-facing path never blocks
# on cold compute. If the warmer stalls (DB outage, etc.), users
# fall through to stale-while-revalidate via `get_stale`.
score_cache = TTLCache(default_ttl=900)


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


async def _enumerate_agents_via_trace_events(
    pool: Any,
    window_days: int,
) -> tuple[list[str], dict[str, str]]:
    """List distinct ``(agent_id_hash, agent_name)`` pairs with traces in the
    window, reading from ``cirislens.trace_events`` (persist 4.0.1's write
    target). Replaces the legacy SELECT against ``cirislens.covenant_traces``
    which is frozen at the 2026-05-02 cutover — CIRISLens#17.

    Returns ``(agent_id_hashes, agent_id_hash → agent_name map)`` consumable
    by ``calculate_fleet_scores_via_persist``.
    """
    from datetime import timedelta  # noqa: PLC0415 — lazy
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(days=window_days)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT agent_id_hash, agent_name
            FROM cirislens.trace_events
            WHERE ts BETWEEN $1 AND $2
              AND agent_id_hash IS NOT NULL
              AND agent_name IS NOT NULL
            """,
            window_start, window_end,
        )
    agent_id_hashes: list[str] = []
    names: dict[str, str] = {}
    for r in rows:
        aid = r["agent_id_hash"]
        if aid in names:
            continue
        agent_id_hashes.append(aid)
        names[aid] = r["agent_name"]
    return agent_id_hashes, names


async def _compute_fleet_scores(window_days: int) -> dict[str, Any]:
    """Compute fleet scores for a window — the work-doing inner half
    of `get_fleet_score`, factored out so the background warmer
    (`_warm_fleet_cache`) and the SWR refresh path share one
    implementation.

    Raises whatever the underlying persist call raises; callers
    decide whether to swallow (warmer) or surface as HTTPException
    (handler). Writes the result to the shared `score_cache`."""
    db_pool = get_db_pool()
    if db_pool is None:
        raise RuntimeError("Database not available")

    engine = persist_engine.get_engine()
    if engine is not None:
        # Post-cutover path (CIRISLens#17). Enumerate agents from the
        # persist write target then batch-score via persist's §E primitive
        # in a single round-trip.
        agent_id_hashes, names = await _enumerate_agents_via_trace_events(
            db_pool, window_days,
        )
        scores = await calculate_fleet_scores_via_persist(
            engine, agent_id_hashes, names, window_days=window_days,
        )
    else:
        # Legacy fallback when persist engine isn't wired (dev / tests).
        scores = await get_fleet_scores(db_pool, window_days)

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
            "ttl_seconds": 900,
        },
    }
    score_cache.set(cache_key("fleet", window_days), result)
    return result


# Per-window asyncio locks so a stale-while-revalidate refresh and a
# concurrent background-warmer pass don't both kick off the same
# 65s CTE. The lock is held only for the SQL window; cache reads stay
# lock-free.
_fleet_refresh_locks: dict[int, asyncio.Lock] = {}

# Set of in-flight SWR background tasks. RUF006: keep a strong
# reference so the task isn't garbage-collected mid-run; auto-discard
# on completion.
_swr_refresh_tasks: set[asyncio.Task[None]] = set()


def _fleet_refresh_lock(window_days: int) -> asyncio.Lock:
    if window_days not in _fleet_refresh_locks:
        _fleet_refresh_locks[window_days] = asyncio.Lock()
    return _fleet_refresh_locks[window_days]


async def _refresh_fleet_in_background(window_days: int) -> None:
    """Recompute fleet scores; never raises (warmer-style)."""
    lock = _fleet_refresh_lock(window_days)
    if lock.locked():
        # Another refresh already in flight for this window — skip.
        logger.debug("Fleet refresh already in flight (window=%d); skipping", window_days)
        return
    async with lock:
        try:
            t0 = time.time()
            result = await _compute_fleet_scores(window_days)
            logger.info(
                "Fleet refresh (window=%d) took %.1fs — %d agents cached for %ds",
                window_days, time.time() - t0,
                result.get("agent_count", 0), result["cache"]["ttl_seconds"],
            )
        except Exception:
            logger.exception("Fleet refresh failed (window=%d) — stale entry remains", window_days)


@router.get("/capacity/fleet")
async def get_fleet_score(
    request: Request,
    window_days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """
    Get CIRIS Capacity Scores for all agents.

    Public endpoint with rate limiting (60/min). Caching strategy:

    - In steady state the background warmer (`_warm_fleet_cache`)
      recomputes every WARMER_INTERVAL seconds and writes to the
      cache with TTL=900s, so user requests always see a warm cache.
    - On cache hit (TTL not yet expired): return immediately.
    - On stale entry (TTL expired but value still in dict): return
      the stale value and kick off a background refresh. The user
      sees ~0.1s, not the 65s of a cold compute. This is the
      stale-while-revalidate fallback for when the warmer is behind.
    - Only on a truly cold cache (worker just started, never
      populated) does the request block on the full compute.

    The 65s cold path is filed against substrate as
    CIRISLensCore#44/#45/#46 (streaming, continuous aggregate,
    internal cache). This lens-side wrapper keeps the user-facing
    latency at ≤ 0.1s until those land.
    """
    check_rate_limit(request)

    key = cache_key("fleet", window_days)
    cached = score_cache.get(key)
    if cached is not None:
        logger.debug("Cache hit for fleet scores (window=%d)", window_days)
        cached["cache"]["cached"] = True
        return cached

    # TTL expired — stale-while-revalidate.
    stale = score_cache.get_stale(key)
    if stale is not None:
        logger.info("Fleet cache stale (window=%d); serving stale + triggering refresh", window_days)
        task = asyncio.create_task(_refresh_fleet_in_background(window_days))
        _swr_refresh_tasks.add(task)
        task.add_done_callback(_swr_refresh_tasks.discard)
        # Mark stale in the response envelope so callers can tell.
        out = dict(stale)
        out["cache"] = {"cached": True, "stale": True, "ttl_seconds": stale["cache"]["ttl_seconds"]}
        return out

    # Truly cold — no entry ever computed. Block on the full compute.
    # Use the same per-window lock so a concurrent warmer doesn't
    # double-spend the 65s.
    lock = _fleet_refresh_lock(window_days)
    async with lock:
        # Re-check the cache: the warmer may have populated it while
        # we were waiting on the lock.
        cached = score_cache.get(key)
        if cached is not None:
            cached["cache"]["cached"] = True
            return cached
        try:
            return await _compute_fleet_scores(window_days)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:
            logger.exception("Error calculating fleet scores")
            raise HTTPException(status_code=500, detail=str(e)) from e


# ============================================================================
# Background warmer
# ============================================================================
#
# Periodic asyncio task that pre-fills the fleet-score cache so the
# user-facing handler always hits a warm entry. WARMER_INTERVAL <
# TTL guarantees the cache never expires during a user request in
# steady state. WARMER_WINDOWS picks the windows operators see on
# the public dashboards (`window_days=7` is the default; `=30` is
# what ciris.ai/ciris-scoring loads first).

WARMER_INTERVAL_SECONDS = 240   # recompute every 4min; pair with TTL=900s above
WARMER_WINDOWS = (7, 30)
_warmer_task: asyncio.Task[None] | None = None


async def _warm_fleet_cache() -> None:
    """Background warmer — runs forever until cancelled."""
    # Initial delay so the first compute happens *after* app startup
    # has settled (engine init, DB pool warmup). The 5s gives the
    # FastAPI app a beat before we hit it with a 65s SQL aggregate.
    await asyncio.sleep(5)
    logger.info(
        "Score warmer starting: windows=%s interval=%ds",
        WARMER_WINDOWS, WARMER_INTERVAL_SECONDS,
    )
    while True:
        for window_days in WARMER_WINDOWS:
            await _refresh_fleet_in_background(window_days)
        await asyncio.sleep(WARMER_INTERVAL_SECONDS)


def start_score_warmer() -> None:
    """Start the background warmer task. Idempotent — callable from
    FastAPI's startup hook (main.py) without checking task state."""
    global _warmer_task  # noqa: PLW0603 — module-level singleton, lifecycle owned by start/stop
    if _warmer_task is not None and not _warmer_task.done():
        return
    _warmer_task = asyncio.create_task(_warm_fleet_cache())


def stop_score_warmer() -> None:
    """Stop the background warmer. Idempotent."""
    global _warmer_task  # noqa: PLW0603 — module-level singleton, lifecycle owned by start/stop
    if _warmer_task is not None and not _warmer_task.done():
        _warmer_task.cancel()
        _warmer_task = None


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
        cached["cache"]["cached"] = True
        return cached

    db_pool = get_db_pool()
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        engine = persist_engine.get_engine()
        if engine is not None:
            # Post-cutover path (CIRISLens#17). Persist §E primitive resolves
            # agent_id_hash internally + computes the score in a single
            # round-trip against trace_events.
            score = await calculate_ciris_score_via_persist(
                engine, agent_name, window_days=window_days,
            )
        else:
            # Legacy fallback when persist engine isn't wired (dev / tests).
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
        cached["cache"]["cached"] = True
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
                    "formula": "R = 1 - drift_penalty (based on absolute change thresholds)",
                    "score": round(score.R.score, 4),
                    "components": {k: round(v, 4) if isinstance(v, float) else v for k, v in score.R.components.items()},
                    "trace_count": score.R.trace_count,
                    "confidence": score.R.confidence,
                    "notes": score.R.notes,
                    "description": "Measures score stability using practical significance thresholds",
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
        cached["cache"]["cached"] = True
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
