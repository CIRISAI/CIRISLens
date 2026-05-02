"""
CIRISPersist Engine singleton for the lens FastAPI process.

Wraps `ciris_persist.Engine` (Rust PyO3 wheel) — the unified persistence
substrate per FSD `CIRISPersist/FSD/CIRIS_PERSIST.md` §3.5.

The Engine is constructed once at FastAPI startup with:
- the lens's TimescaleDB DSN (CIRISLENS_DB_URL or DATABASE_URL),
- a deployment-stable `signing_key_id` (CIRISLENS_SCRUB_KEY_ID, default
  `lens-scrub-v1`); ciris-keyring stores the seed in
  hardware-backed storage where available (TPM 2.0 / Linux Secret
  Service / etc.), SoftwareSigner fallback otherwise. The seed never
  crosses the FFI boundary; the lens process never holds private bytes.

The same key plays three roles per CIRISPersist §1 + PoB §3.2 — scrub
envelope signer, registry-published lens identity, and (Phase 2.3)
the deployment's Reticulum destination. `engine.public_key_b64()` is
what gets published to CIRISRegistry at deploy time.

## Concurrent-worker boot serialization

Multi-worker uvicorn deployments construct `Engine` once per worker
process. ciris-persist v0.1.4's migration runner does not yet take
its own advisory lock — concurrent workers race on `assert migrations
table` and N-1 of them fail. The race is fixed upstream in v0.1.5
(see CIRISPersist `run_migrations` PR), but until that ships we
serialize lens-side: a per-deploy `pg_advisory_lock` held across the
`cp.Engine()` call. First worker to enter the function holds the
lock, runs migrations + bootstraps keyring; the rest queue up,
acquire the lock in turn, and find migrations already applied.
~50-200 ms wait per worker on cold start.

The lock is released on Engine success OR failure (via finally), and
the lock connection is short-lived — if a worker panics mid-Engine,
session close releases the lock automatically.

## Environment

- CIRISLENS_DB_URL   — preferred DSN. Falls back to DATABASE_URL.
- CIRISLENS_SCRUB_KEY_ID — keyring alias for the lens identity
  (default `lens-scrub-v1`).
- CIRISLENS_PERSIST_DISABLED — if set to truthy, skip Engine
  construction. Lens then falls back to the legacy ingest path.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import asyncpg

if TYPE_CHECKING:  # pragma: no cover
    from ciris_persist import Engine

logger = logging.getLogger(__name__)

# Per-deploy advisory lock id for the Engine-init / migration phase.
# Must be a stable int64. Bytes "LENSMIGR" interpreted as ASCII.
# Independent of any namespace ciris-persist may use internally for
# its own intra-Engine locking — that's a separate concern (and
# v0.1.5 will add it).
_MIGRATION_LOCK_ID = 0x4C454E534D494752  # "LENSMIGR"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def _credential_free_dsn_label(dsn: str) -> str:
    """Return host:port/dbname from a DSN — credentials elided.

    Surfaced at Engine init so operators can confirm without bashing
    into the container that persist queries the same database the
    legacy ingest path uses. The full DSN may carry passwords; only
    the connection target is logged.
    """
    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        parsed = urlparse(dsn)
        return f"{parsed.hostname}:{parsed.port or '?'}{parsed.path}"
    except Exception:  # noqa: BLE001
        return "<unparseable>"


class _State:
    """Module-singleton state. Class-as-namespace avoids `global`
    statements while keeping the public surface (initialize / get_engine
    / status) function-shaped for callers."""

    engine: Engine | None = None
    disabled: bool = False
    init_error: str | None = None
    # True when the lens scrubber callback wired successfully into
    # Engine. False means Engine is using NullScrubber (correct at
    # GENERIC, unsafe at higher levels — handler must refuse).
    scrubber_ready: bool = False


async def initialize() -> Engine | None:
    """
    Construct the global Engine. Idempotent; safe to call on every
    startup hook. Returns None when:
    - CIRISLENS_PERSIST_DISABLED is truthy, OR
    - the `ciris_persist` wheel is not installed, OR
    - DSN is unset, OR
    - `cp.Engine()` raises (Postgres unreachable, migration race,
      keyring inaccessible) — error captured in `_State.init_error`
      and surfaced via `/health`; the worker continues without
      persist.
    """
    if _State.engine is not None:
        return _State.engine

    if _truthy(os.getenv("CIRISLENS_PERSIST_DISABLED")):
        _State.disabled = True
        logger.warning("CIRISLENS_PERSIST_DISABLED is set; falling back to legacy ingest path")
        return None

    dsn_source = "CIRISLENS_DB_URL" if os.getenv("CIRISLENS_DB_URL") else "DATABASE_URL"
    dsn = os.getenv("CIRISLENS_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        _State.init_error = "neither CIRISLENS_DB_URL nor DATABASE_URL is set"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None
    dsn_label = _credential_free_dsn_label(dsn)

    try:
        import ciris_persist as cp  # noqa: PLC0415  — lazy import; may be absent in dev
    except ImportError as e:
        _State.init_error = f"ciris_persist wheel not installed: {e}"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None

    key_id = os.getenv("CIRISLENS_SCRUB_KEY_ID", "lens-scrub-v1")

    # Wire the lens scrubber as persist's scrubber callback. Persist
    # bypasses it at trace_level=generic (content-free); at detailed/
    # full_traces, the callable runs cirislens_core PII scrub + the
    # security sanitizer. Constructed once per Engine (i.e. once per
    # worker process) — holds no per-request state.
    try:
        import lens_scrubber  # noqa: PLC0415  — lazy; depends on cirislens_core / spaCy availability
        scrubber_cb = lens_scrubber.make_persist_scrubber()
        logger.info("Lens scrubber wired into Engine")
    except Exception as e:
        # If the scrubber pipeline can't load (model files missing,
        # etc.), persist falls back to NullScrubber — which is correct
        # at GENERIC and emits a tracing::warn at higher levels. The
        # lens handler should refuse non-generic ingest in that state;
        # see _State.scrubber_ready.
        logger.error("Lens scrubber NOT wired — non-generic ingest will be rejected: %s", e)
        scrubber_cb = None
        _State.scrubber_ready = False
    else:
        _State.scrubber_ready = True

    logger.info(
        "Constructing ciris_persist.Engine: version=%s schemas=%s key_id=%s "
        "scrubber=%s dsn_source=%s dsn_target=%s",
        cp.__version__,
        cp.SUPPORTED_SCHEMA_VERSIONS,
        key_id,
        "wired" if scrubber_cb is not None else "null",
        dsn_source,
        dsn_label,
    )

    # Serialize across uvicorn workers via Postgres advisory lock —
    # see module docstring §"Concurrent-worker boot serialization".
    lock_conn: asyncpg.Connection | None = None
    try:
        lock_conn = await asyncpg.connect(dsn)
        await lock_conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        logger.debug("acquired migration advisory lock %#x", _MIGRATION_LOCK_ID)

        try:
            engine = cp.Engine(dsn=dsn, signing_key_id=key_id, scrubber=scrubber_cb)
        except Exception as e:
            # Catch every engine-init failure mode — RuntimeError from
            # PyO3, schema-version mismatch, keyring inaccessible, etc.
            # — and surface via _State.init_error rather than crashing
            # the worker.
            _State.init_error = f"{type(e).__name__}: {e}"
            logger.error("ciris_persist.Engine init failed: %s", _State.init_error)
            return None
    finally:
        # Release lock + close lock-conn even on failure. Session
        # close auto-releases the advisory lock if the explicit
        # unlock didn't run (e.g. on connection error).
        if lock_conn is not None:
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
            except Exception as e:
                logger.warning("advisory_unlock failed (lock will release on conn close): %s", e)
            await lock_conn.close()

    pub_b64 = engine.public_key_b64()
    logger.info(
        "ciris_persist.Engine ready: lens_pubkey=%s... (%d b64 chars) "
        "— publish to CIRISRegistry as the lens identity",
        pub_b64[:32],
        len(pub_b64),
    )

    _State.engine = engine
    return _State.engine


def get_engine() -> Engine | None:
    """Return the initialized Engine, or None when persist is disabled
    / not yet initialized. Callers must handle None to fall back to
    the legacy ingest path."""
    return _State.engine


def status() -> dict[str, Any]:
    """For /health and admin diagnostics."""
    return {
        "initialized": _State.engine is not None,
        "disabled": _State.disabled,
        "init_error": _State.init_error,
        "scrubber_ready": _State.scrubber_ready,
    }


def scrubber_ready() -> bool:
    """True iff the lens scrubber wired into Engine successfully.
    Handler must refuse non-generic trace ingest when this is False
    (NullScrubber would let PII land unscrubbed at detailed/full_traces)."""
    return _State.scrubber_ready
