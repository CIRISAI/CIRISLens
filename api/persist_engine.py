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
- CIRISLENS_SCRUB_KEY_ID — keyring alias for the lens scrub identity
  (default `lens-scrub-v1`). P-256, signs per-row scrub envelopes
  on trace_events.
- CIRISLENS_STEWARD_KEY_ID — federation steward identifier (default
  `lens-steward`). Distinct from the scrub identity per persist
  v0.2.2 separation: Ed25519, signs federation_keys / federation_*
  rows the lens publishes.
- CIRISLENS_STEWARD_KEY_PATH — filesystem path to the 32-byte raw
  Ed25519 seed for the steward identity. Bridge generates the
  keypair offline + vaults it; the path here is whatever bind-mount
  the deployment exposes (e.g. /run/secrets/lens-steward, or a
  Docker secret). Must be readable by uid 1000 (cirislens user) and
  not world-readable.
- CIRISLENS_PERSIST_DISABLED — if set to truthy, skip Engine
  construction. Lens then falls back to the legacy ingest path.

Both-or-neither steward construction: persist v0.2.2 raises
ValueError if exactly one of steward_key_id/path is set. When neither
is set, federation-mirror writes from `federation_mirror.py` no-op
(legacy-only path); when both are set, every agent registration is
mirrored into federation_keys signed by the steward.
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


def _resolve_dsn_and_module() -> tuple[str | None, str | None, object | None]:
    """Return `(dsn, dsn_source_label, ciris_persist_module)` for Engine
    construction, or `(None, None, None)` after recording the reason in
    `_State.init_error`. Handles the two early-exit paths (DSN unset,
    wheel not installed) so `initialize()` stays under the function-
    size lint thresholds."""
    dsn_source = "CIRISLENS_DB_URL" if os.getenv("CIRISLENS_DB_URL") else "DATABASE_URL"
    dsn = os.getenv("CIRISLENS_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        _State.init_error = "neither CIRISLENS_DB_URL nor DATABASE_URL is set"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None, None, None
    try:
        import ciris_persist as cp  # noqa: PLC0415  — lazy; may be absent in dev
    except ImportError as e:
        _State.init_error = f"ciris_persist wheel not installed: {e}"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None, None, None
    return dsn, dsn_source, cp


def _wire_scrubber() -> object | None:
    """Build the lens scrubber callback for persist's Engine. Returns
    None when the pipeline can't load (model bundle missing, etc.) —
    persist falls back to NullScrubber, which is correct at GENERIC
    and emits a tracing::warn at higher levels. The handler refuses
    non-generic ingest in that state via `_State.scrubber_ready`.
    """
    try:
        import lens_scrubber  # noqa: PLC0415  — lazy
        callback = lens_scrubber.make_persist_scrubber()
    except Exception as e:
        logger.error("Lens scrubber NOT wired — non-generic ingest will be rejected: %s", e)
        _State.scrubber_ready = False
        return None
    else:
        logger.info("Lens scrubber wired into Engine")
        _State.scrubber_ready = True
        return callback


def _resolve_steward_args() -> tuple[str | None, str | None, str | None]:
    """Read CIRISLENS_STEWARD_KEY_ID + CIRISLENS_STEWARD_KEY_PATH and
    return `(key_id, key_path, error)` to pass to the Engine
    constructor. The third tuple element is set when configuration is
    invalid (path set but file missing) — caller surfaces it via
    `_State.init_error` and aborts initialization.

    When the path env var is unset (the common case until bridge ships
    the steward keypair), returns `(None, None, None)` — Engine is
    constructed without a federation steward, federation_mirror writes
    will no-op, and the lens runs accord_public_keys-only.
    """
    from pathlib import Path  # noqa: PLC0415  — lazy

    steward_key_id = os.getenv("CIRISLENS_STEWARD_KEY_ID", "lens-steward")
    steward_key_path = os.getenv("CIRISLENS_STEWARD_KEY_PATH")
    if steward_key_path is None:
        logger.info(
            "Federation steward not configured (CIRISLENS_STEWARD_KEY_PATH unset); "
            "running accord_public_keys-only — federation_mirror writes will no-op"
        )
        return None, None, None
    if not Path(steward_key_path).exists():
        # Path set but file missing — almost always a deploy-config
        # issue (secret-mount missing, wrong path). Surface loudly.
        return None, None, f"CIRISLENS_STEWARD_KEY_PATH={steward_key_path} not found"
    logger.info(
        "Federation steward configured: key_id=%s key_path=%s",
        steward_key_id,
        steward_key_path,
    )
    return steward_key_id, steward_key_path, None


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
    except Exception:
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
    # True when the federation steward identity is configured (both
    # steward_key_id and steward_key_path were present and Engine
    # accepted them). When False, federation_mirror writes no-op and
    # the lens runs accord_public_keys-only (legacy path) — still
    # works because persist's Backend dual-reads both tables on
    # verify (v0.2.1+).
    steward_ready: bool = False


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

    dsn, dsn_source, cp = _resolve_dsn_and_module()
    if dsn is None:
        return None
    dsn_label = _credential_free_dsn_label(dsn)

    key_id = os.getenv("CIRISLENS_SCRUB_KEY_ID", "lens-scrub-v1")

    # v0.2.2 steward identity: federation steward (Ed25519). Both-or-
    # neither — passing only one raises ValueError on the persist side.
    # We pre-validate via the helper so the error message points at
    # our env vars rather than at persist's constructor signature.
    steward_key_id_arg, steward_key_path_arg, steward_err = _resolve_steward_args()
    if steward_err is not None:
        _State.init_error = steward_err
        logger.error("Refusing init: %s", steward_err)
        return None

    # Persist bypasses the scrubber callback at trace_level=generic
    # (content-free); at detailed/full_traces the callable runs
    # cirislens_core PII scrub + the security sanitizer. Helper sets
    # _State.scrubber_ready as a side effect.
    scrubber_cb = _wire_scrubber()

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
            engine = cp.Engine(
                dsn=dsn,
                signing_key_id=key_id,
                scrubber=scrubber_cb,
                steward_key_id=steward_key_id_arg,
                steward_key_path=steward_key_path_arg,
            )
            _State.steward_ready = steward_key_path_arg is not None
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
        "steward_ready": _State.steward_ready,
    }


def scrubber_ready() -> bool:
    """True iff the lens scrubber wired into Engine successfully.
    Handler must refuse non-generic trace ingest when this is False
    (NullScrubber would let PII land unscrubbed at detailed/full_traces)."""
    return _State.scrubber_ready


def steward_ready() -> bool:
    """True iff the federation steward identity is configured. When
    False, federation_mirror writes no-op (legacy-only path)."""
    return _State.steward_ready
