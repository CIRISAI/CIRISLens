"""
Lens Federation Identity API

Publishes the deployed lens's full hybrid federation identity in one
call, per CIRISLens#20. Substrate is `Engine.local_identity_aggregate`
(CIRISPersist#198, shipped in persist v5.4 / v5.5 — JSON-encoded
`LocalIdentityAggregate` covering all three §5.6.8.8.2 keypair roles).

Public endpoint, no auth, generously cached — the identity is stable
per worker process (steward seed doesn't rotate at runtime; the
content-KEM keypair is freshly minted on first call and persist-sealed
across reboots).

Three role bundles in the response:

- **Signing** (Ed25519 + ML-DSA-65) — persist's local signer, the
  identity that signs every accord trace receipt. Ed25519 always
  present; ML-DSA-65 present whenever `CIRISLENS_STEWARD_PQC_KEY_PATH`
  was configured at Engine init.
- **RET-transport** (X25519 + Ed25519) — Reticulum federation transport
  identity. Read from `edge.transport_identity_pubkeys()` (ciris-edge
  >= 2.2.2) when the Edge runtime was initialized at startup (env
  `CIRISLENS_EDGE_IDENTITY_PATH` set). `None` when Edge is not
  configured. Edge auto-generates the Reticulum identity file on
  first run; stable thereafter.
- **Content-KEM** (X25519 + ML-KEM-768) — freshly minted, persist-
  sealed content-encryption keypair, stable across calls + reboots.
  Available on every persist >= 5.4 deployment (no Edge needed). This
  is the HnDl-resistance PQ surface — content the lens encrypts to
  peers can stay confidential against a quantum adversary harvesting
  today's transport.

Plus `identity_hash` (collision-safe SHA-256 over present role pubkeys,
role-labeled + length-prefixed — a stable addressing primitive that
peers can use to recognize this specific lens identity across rotation
events).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

import edge_runtime
import persist_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["identity"])


@router.get("/identity")
async def get_lens_identity() -> dict[str, Any]:
    """Return the deployed lens's full federation hybrid identity.

    Public, no auth. Stable per worker process — operators can curl
    this once and pin the identity_hash + role pubkeys into a peer's
    bootstrap configuration. Returns JSON shape per persist's
    `LocalIdentityAggregate` (CIRISPersist#198) v1.

    When the Edge runtime was initialized at startup
    (`CIRISLENS_EDGE_IDENTITY_PATH` set), the response carries the
    full 6-key bundle including the Reticulum transport pubkeys
    (X25519 + Ed25519). When Edge is not configured, the response
    is the 4-of-6 bundle — signing (Ed25519 + ML-DSA-65) + content-KEM
    (X25519 + ML-KEM-768) — peer addressing for signing + content
    encryption (including HnDl-resistance ML-KEM-768) is still
    covered; only Reticulum direct-transport addressing is absent.
    """
    engine = persist_engine.get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="persist engine unavailable")

    # If the Edge runtime is configured (CIRISLENS_EDGE_IDENTITY_PATH
    # set on startup), fold its transport pubkeys into the aggregate
    # so the response carries the full 6-key bundle. Otherwise pass
    # None args and persist emits 4-of-6 (Reticulum fields null).
    transport_x25519: str | None = None
    transport_ed25519: str | None = None
    edge = edge_runtime.get_edge()
    if edge is not None:
        try:
            pubkeys = edge.transport_identity_pubkeys()
            transport_x25519 = pubkeys["x25519_pub_base64"]
            transport_ed25519 = pubkeys["ed25519_pub_base64"]
        except Exception as e:
            logger.warning(
                "Edge transport_identity_pubkeys() failed: %s; emitting 4-of-6 bundle",
                e,
            )

    try:
        agg_json = engine.local_identity_aggregate(transport_x25519, transport_ed25519)
    except ValueError as e:
        # No signer configured (Ed25519 path) — operational deploy bug.
        raise HTTPException(status_code=503, detail=f"local signer unavailable: {e}") from e
    except RuntimeError as e:
        # Backend / IO error from the substrate. Bubble as transient.
        logger.exception("local_identity_aggregate failed")
        raise HTTPException(
            status_code=503, detail=str(e), headers={"Retry-After": "5"},
        ) from e

    return json.loads(agg_json)
