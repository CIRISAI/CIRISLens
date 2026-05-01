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

Environment:
- CIRISLENS_DB_URL   — preferred DSN. Falls back to DATABASE_URL.
- CIRISLENS_SCRUB_KEY_ID — keyring alias for the lens identity
  (default `lens-scrub-v1`).
- CIRISLENS_PERSIST_DISABLED — if set to truthy, skip Engine
  construction (degraded mode for environments where the wheel isn't
  available, e.g. local dev without TimescaleDB). Lens then falls
  back to the legacy ingest path. THREAT_MODEL.md AV-26 (forthcoming):
  must be off in production deployments.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ciris_persist import Engine

logger = logging.getLogger(__name__)


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


class _State:
    """Module-singleton state. Class-as-namespace avoids `global`
    statements while keeping the public surface (initialize / get_engine
    / status) function-shaped for callers."""

    engine: Engine | None = None
    disabled: bool = False
    init_error: str | None = None


def initialize() -> Engine | None:
    """
    Construct the global Engine. Idempotent; safe to call on every
    startup hook. Returns None when:
    - CIRISLENS_PERSIST_DISABLED is truthy, OR
    - the `ciris_persist` wheel is not installed, OR
    - DSN is unset.

    Raises RuntimeError on hard errors (Postgres unreachable,
    migrations fail, keyring inaccessible) — those should fail the
    deploy fast rather than silently degrade.
    """
    if _State.engine is not None:
        return _State.engine

    if _truthy(os.getenv("CIRISLENS_PERSIST_DISABLED")):
        _State.disabled = True
        logger.warning("CIRISLENS_PERSIST_DISABLED is set; falling back to legacy ingest path")
        return None

    dsn = os.getenv("CIRISLENS_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        _State.init_error = "neither CIRISLENS_DB_URL nor DATABASE_URL is set"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None

    try:
        import ciris_persist as cp  # noqa: PLC0415  — lazy import; may be absent in dev
    except ImportError as e:
        _State.init_error = f"ciris_persist wheel not installed: {e}"
        logger.warning("ciris-persist not initialized: %s", _State.init_error)
        return None

    key_id = os.getenv("CIRISLENS_SCRUB_KEY_ID", "lens-scrub-v1")
    logger.info(
        "Constructing ciris_persist.Engine: version=%s schemas=%s key_id=%s",
        cp.__version__,
        cp.SUPPORTED_SCHEMA_VERSIONS,
        key_id,
    )

    # Engine construction runs migrations (V001+V003 today) and
    # bootstraps the keyring identity. Fail-fast on any issue.
    engine = cp.Engine(dsn=dsn, signing_key_id=key_id)

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
    }
