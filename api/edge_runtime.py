"""
Lens Edge runtime — minimal cohabitation init for transport identity.

CIRISLens#20 needs the lens to publish the full 6-key
`LocalIdentityAggregate` (CIRISPersist#199), which includes the
Reticulum transport role (X25519 + Ed25519). Those two pubkeys are
owned by the Edge runtime, not by persist directly — they're a
property of the Reticulum identity Edge mints at bootstrap.

This module brings up an Edge runtime purely to expose the transport
identity. It does NOT call `ciris_lens_core.install_relay(edge)` —
that path is still gated on CIRISLensCore#43.1's cross-cdylib Type
mismatch on the wheel-built path. The agent team uses install_relay
via their own bootstrap; the deployed lens has its own FastAPI ingest
path and doesn't need to hook into Edge's `AccordEventsBatch`
delivery for this commit.

What Edge does in the deployed lens process:

- Reads / mints a Reticulum identity at `CIRISLENS_EDGE_IDENTITY_PATH`.
  If the file doesn't exist, Edge generates a fresh keypair and writes
  it (mode 0600). Stable across restarts thereafter.
- Runs Reticulum's default transport (announces every 5min by default).
  Without bootstrap_peers configured, announces go nowhere; the
  process activity is harmless background noise.
- Exposes `transport_identity_pubkeys()` returning
  `{x25519_pub_base64, ed25519_pub_base64}` for `/api/v1/identity` to
  fold into persist's `local_identity_aggregate(tx, te)` call.

Edge initialization is **optional**: if `CIRISLENS_EDGE_IDENTITY_PATH`
is unset, `initialize()` no-ops and the identity endpoint falls back
to the 4-of-6-key bundle (Ed25519 + ML-DSA-65 + content-KEM X25519 +
ML-KEM-768). The lens continues to function normally.

For prod: set `CIRISLENS_EDGE_IDENTITY_PATH` to a persistent path
(typically alongside the existing steward key, e.g.
`/var/lib/cirislens/keyring/lens-edge.identity`).
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import persist_engine

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class _State:
    """Module-singleton for the Edge handle."""
    edge: Any = None
    init_error: str | None = None


def get_edge() -> Any:
    """Return the live Edge handle, or None if initialization was
    skipped or failed. Callers gate on `is not None` before reading
    transport_identity_pubkeys()."""
    return _State.edge


def initialize_sync() -> Any:
    """Construct the Edge runtime if `CIRISLENS_EDGE_IDENTITY_PATH`
    is set; return the Edge handle (or None on skip / failure).

    Synchronous because `ciris_edge.init_edge_runtime` is a sync
    PyO3 call. Callers from async context should run this in an
    executor if they're concerned about blocking the loop; in
    practice startup-hook callers don't care.

    Idempotent — re-calling returns the existing handle without
    re-init. The error path (logged WARN, returns None) is non-fatal:
    the lens functions normally without Edge, just emits a 4-of-6
    identity bundle.
    """
    if _State.edge is not None:
        return _State.edge

    identity_path = os.environ.get("CIRISLENS_EDGE_IDENTITY_PATH")
    if not identity_path:
        logger.info(
            "Edge runtime not configured (CIRISLENS_EDGE_IDENTITY_PATH unset); "
            "/api/v1/identity will emit a 4-of-6-key bundle without Reticulum "
            "transport pubkeys. Set the env var to enable.",
        )
        return None

    eng = persist_engine.get_engine()
    if eng is None:
        _State.init_error = "persist engine not yet initialized"
        logger.warning(
            "Edge runtime init skipped: %s. Call after persist_engine.initialize().",
            _State.init_error,
        )
        return None

    try:
        import ciris_edge  # noqa: PLC0415 — lazy; may be absent in dev
    except ImportError as e:
        _State.init_error = f"ciris_edge wheel not installed: {e}"
        logger.warning("Edge runtime init skipped: %s", _State.init_error)
        return None

    try:
        logger.info(
            "Constructing ciris_edge.Edge: version=%s identity_path=%s",
            ciris_edge.__version__, identity_path,
        )
        # Reticulum default transport; no bootstrap_peers configured
        # (the deployed lens isn't federating over Reticulum yet —
        # CIRISLens#18 §2 deeper scope). Edge generates the identity
        # file at the path if missing; reuses if present.
        _State.edge = ciris_edge.init_edge_runtime(
            eng,
            identity_path=identity_path,
        )
    except Exception as e:
        _State.init_error = f"{type(e).__name__}: {e}"
        logger.warning("Edge runtime init failed: %s", _State.init_error)
        return None

    pubkeys = _State.edge.transport_identity_pubkeys()
    logger.info(
        "Edge runtime ready: x25519=%s... ed25519=%s...",
        pubkeys["x25519_pub_base64"][:16],
        pubkeys["ed25519_pub_base64"][:16],
    )
    return _State.edge


def get_init_error() -> str | None:
    """Diagnostic: returns the last init error message, or None if
    init succeeded or wasn't attempted."""
    return _State.init_error
