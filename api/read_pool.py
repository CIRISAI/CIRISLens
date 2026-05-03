"""
Read-only connection pool for **peer-context** consumers — NOT for
lens-core handlers.

## Who actually uses this

This pool is intentionally narrow. It backs the v0.3.2
`cirislens_reader` PostgreSQL role (CIRISPersist#9 / V005 +
`docs/PUBLIC_SCHEMA_CONTRACT.md`) for code that reads persist's data
**as a peer**, not as the owner:

- `scripts/export_qa_27_9.py` — runs from operator machines or as
  `docker exec` inside the lens-api container; it consumes lens's
  data without being lens-core itself.
- Future ops scripts (PQC backfill, audit replay, corpus snapshot
  builders) that run separately from the lens-api process.
- Future federation peers reading lens's data via direct connection
  (RATCHET research, partner sites).

## Who does NOT use this

Lens-core handlers in `accord_api.py` / `accord_api_v2.py` /
`scoring_api.py` use `db_pool` directly via `get_db_pool()`. They
are persist's owner — lens-core constructed the Engine, holds the
write DSN, has full privilege by relationship. Routing in-process
analytical queries through a SELECT-only role would add plumbing
without a meaningful security boundary (lens and persist are the
same trust domain).

The peer/owner distinction matters: cirislens_reader is a
defense-in-depth boundary BETWEEN persist and external consumers,
not WITHIN lens-core.

## Lifecycle

`initialize()` is idempotent and called from `main.py` startup. The
pool stays available for any in-process script invocation that
chooses to use it (none today; export script runs separately and
sets its own `CIRISLENS_READ_DSN`).

When `CIRISLENS_READ_DSN` is unset, `initialize()` no-ops and
`get_pool()` returns None. The export script handles None by
refusing to run (correct posture: peer-context code MUST connect
through the SELECT-only role, never fall back to the owner DSN).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class _State:
    """Module-singleton state. Same shape as `persist_engine._State`."""

    pool: asyncpg.Pool | None = None
    init_error: str | None = None
    dsn_configured: bool = False


def _credential_free_dsn_label(dsn: str) -> str:
    """Return host:port/dbname from a DSN — credentials elided.
    Mirrors `persist_engine._credential_free_dsn_label`."""
    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        parsed = urlparse(dsn)
        return f"{parsed.hostname}:{parsed.port or '?'}{parsed.path}"
    except Exception:
        return "<unparseable>"


async def initialize() -> asyncpg.Pool | None:
    """Create the read pool from `CIRISLENS_READ_DSN`. Idempotent.

    Returns None when:
    - `CIRISLENS_READ_DSN` is unset (graceful fallback to write pool)
    - asyncpg can't connect (recorded in `_State.init_error`; lens
      continues with read-pool=None and falls back to write pool)
    """
    if _State.pool is not None:
        return _State.pool

    dsn = os.getenv("CIRISLENS_READ_DSN")
    if not dsn:
        logger.info(
            "CIRISLENS_READ_DSN unset — analytical reads will use the "
            "write pool (graceful degradation). Bridge prereq: provision "
            "a cirislens_analytics login user with GRANT cirislens_reader "
            "(per CIRISPersist v0.3.2 V005 + PUBLIC_SCHEMA_CONTRACT.md), "
            "then set CIRISLENS_READ_DSN to the login DSN."
        )
        _State.dsn_configured = False
        return None

    import asyncpg  # noqa: PLC0415

    try:
        # min_size=1 because reads can be bursty; max_size=10 matches
        # write-pool limit (deployment doesn't expect more than ~20
        # concurrent DB consumers across both pools).
        # command_timeout=30s guards analytical-query-runs-forever
        # against eating connections from the read pool.
        _State.pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=10, command_timeout=30,
        )
        _State.dsn_configured = True
    except Exception as e:
        _State.init_error = f"{type(e).__name__}: {e}"
        logger.error(
            "Read pool init failed: %s — falling back to write pool",
            _State.init_error,
        )
        return None

    logger.info(
        "Read pool created (cirislens_reader role): dsn_target=%s",
        _credential_free_dsn_label(dsn),
    )
    return _State.pool


def get_pool() -> asyncpg.Pool | None:
    """Return the read pool, or None when no read DSN is configured.

    Callers using this as a primary should wrap:

        pool = read_pool.get_pool() or get_db_pool()

    so analytical reads route through the SELECT-only role when it's
    available and gracefully fall back to the write pool when it's
    not.
    """
    return _State.pool


def is_configured() -> bool:
    """True iff `CIRISLENS_READ_DSN` is set AND the pool initialized
    successfully. Surfaced via /health for operator visibility."""
    return _State.dsn_configured


def status() -> dict[str, object]:
    """For /health and admin diagnostics."""
    return {
        "configured": _State.dsn_configured,
        "initialized": _State.pool is not None,
        "init_error": _State.init_error,
    }


async def close() -> None:
    """Close the read pool on FastAPI shutdown."""
    if _State.pool is not None:
        await _State.pool.close()
        _State.pool = None
        _State.dsn_configured = False
