#!/usr/bin/env python3
"""
One-shot backfill: cirislens.accord_public_keys → cirislens.federation_keys.

Run after the bridge has bootstrapped the `lens-steward` row in
federation_keys. Reads every active row from accord_public_keys (NOT
revoked, NOT expired) and writes a corresponding federation_keys row
signed by `lens-steward`. Idempotent — skips keys already present in
federation_keys (lookup_public_key check), so safe to re-run after a
partial failure.

The hot path (`api/federation_mirror.py`) handles new registrations
going forward. This script is the one-time catch-up for keys that
existed before the federation directory came online.

## Usage

Inside a lens-api container that has CIRISLENS_STEWARD_KEY_PATH set
and the steward seed mounted:

    docker exec -it cirislens-api \\
        python /app/scripts/migrate_pubkeys_to_federation.py [--dry-run]

Environment (all read by `persist_engine.initialize()`):
- CIRISLENS_DB_URL (or DATABASE_URL) — production cirislens DSN
- CIRISLENS_STEWARD_KEY_PATH — path to the 32-byte raw Ed25519 seed
- CIRISLENS_STEWARD_KEY_ID — defaults to `lens-steward`

## Envelope shape

Backfill envelopes carry forensic provenance distinguishing them from
hot-path registrations. The shape includes `"backfilled_from":
"accord_public_keys"` and the original `created_at` so an auditor can
join federation_keys rows back to accord_public_keys without ambiguity.

`valid_from` is set to the original `accord_public_keys.created_at`
(NOT `now()`) so the federation_keys validity window matches the
historical truth. `valid_until` mirrors `accord_public_keys.expires_at`
(`None` when the column is null).

## What it skips

- Revoked keys (`accord_public_keys.revoked_at IS NOT NULL`). These
  were never going to verify anyway; the legacy table keeps them for
  forensics. Federation directory mirrors active set only.
- Expired keys (`expires_at < NOW()`). Same reasoning — verify
  filter excludes them.
- Keys already in federation_keys (lookup_public_key returns Some).
  Idempotent re-run.

## Failure handling

Per-row failures (FK violation if lens-steward not bootstrapped, FFI
error from steward_sign, etc.) are logged and counted but don't abort
the batch. Exit code reflects whether any failures occurred:
- 0 = all rows succeeded or skipped
- 1 = at least one row failed (re-run after fixing)
- 2 = pre-flight failed (steward not configured, DB unreachable)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add api/ to path so persist_engine + federation_mirror imports work.
_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

import asyncpg  # noqa: E402

import persist_engine  # noqa: E402

logger = logging.getLogger("migrate_pubkeys")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_backfill_envelope(row: asyncpg.Record) -> dict[str, object]:
    """Construct the canonical envelope for a backfilled accord_public_keys row.

    Field ordering doesn't affect canonicalization (PythonJsonDumpsCanonicalizer
    sorts keys), but the dict semantics matter: `backfilled_from` and
    `original_created_at` are the forensic markers that distinguish a
    migrator-written row from a hot-path registration."""
    envelope: dict[str, object] = {
        "registrar": "lens",
        "role": "agent_trace_signing",
        "backfilled_from": "accord_public_keys",
        "key_id": row["key_id"],
        "pubkey_ed25519_base64": row["public_key_base64"],
        "original_created_at": row["created_at"].isoformat(),
    }
    if row["description"]:
        envelope["description"] = row["description"]
    if row["added_by"]:
        envelope["original_added_by"] = row["added_by"]
    return envelope


def _build_signed_record(
    *,
    engine: object,
    row: asyncpg.Record,
) -> dict[str, object]:
    """Build the SignedKeyRecord payload (minus the outer `{"record": ...}`).

    Encapsulates the canonicalize → hash → sign → assemble flow for a
    single backfill row. Raising propagates to the caller's try/except."""
    envelope = _build_backfill_envelope(row)
    canonical = engine.canonicalize_envelope(json.dumps(envelope))  # type: ignore[attr-defined]
    sig_raw = engine.steward_sign(canonical)  # type: ignore[attr-defined]
    sig_b64 = base64.b64encode(sig_raw).decode("ascii")

    valid_until: str | None = None
    if row["expires_at"] is not None:
        valid_until = row["expires_at"].isoformat()

    return {
        "key_id": row["key_id"],
        "pubkey_ed25519_base64": row["public_key_base64"],
        "algorithm": "hybrid",
        "identity_type": "agent",
        "identity_ref": row["key_id"],
        "valid_from": row["created_at"].isoformat(),
        "valid_until": valid_until,
        "registration_envelope": envelope,
        "original_content_hash": hashlib.sha256(canonical).hexdigest(),
        "scrub_signature_classical": sig_b64,
        "scrub_key_id": engine.steward_key_id(),  # type: ignore[attr-defined]
        "scrub_timestamp": _utc_now_iso(),
        "persist_row_hash": "",
    }


async def _fetch_active_keys(dsn: str) -> list[asyncpg.Record]:
    """Return active accord_public_keys rows in created_at order. The
    ORDER BY makes re-runs deterministic — if the migrator partially
    fails, the next run resumes in the same order, hitting the
    lookup_public_key fast-path on the rows that already landed."""
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            """
            SELECT key_id, public_key_base64, description,
                   created_at, expires_at, added_by
            FROM cirislens.accord_public_keys
            WHERE revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at
            """,
        )
    finally:
        await conn.close()


async def main(dry_run: bool) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Engine init — same code path as the lens-api worker uses, so
    # this script's environment must match a worker's environment
    # (DSN + steward seed).
    engine = await persist_engine.initialize()
    if engine is None:
        logger.error("Engine init failed: %s", persist_engine.status())
        return 2
    if not persist_engine.steward_ready():
        logger.error(
            "Steward not configured — set CIRISLENS_STEWARD_KEY_PATH "
            "to the lens-steward Ed25519 seed before running migrator",
        )
        return 2

    dsn = os.getenv("CIRISLENS_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DSN unset (CIRISLENS_DB_URL / DATABASE_URL)")
        return 2

    rows = await _fetch_active_keys(dsn)
    logger.info("Fetched %d active rows from accord_public_keys", len(rows))

    counts = {"backfilled": 0, "skipped_existing": 0, "failed": 0}
    for row in rows:
        key_id = row["key_id"]

        # Idempotency — federation_keys.lookup_public_key reads via the
        # Federation trait (federation_keys table only, not the dual-
        # read fallback). Some(...) means the row is already there.
        existing = engine.lookup_public_key(key_id)  # type: ignore[attr-defined]
        if existing is not None:
            counts["skipped_existing"] += 1
            logger.debug("skip existing %s", key_id)
            continue

        try:
            record = _build_signed_record(engine=engine, row=row)
            if dry_run:
                logger.info("[dry-run] would backfill %s (created_at=%s)",
                            key_id, row["created_at"].isoformat())
            else:
                engine.put_public_key(json.dumps({"record": record}))  # type: ignore[attr-defined]
                logger.info("backfilled %s", key_id)
        except Exception as e:
            counts["failed"] += 1
            logger.exception("FAILED %s: %s", key_id, e)
            continue
        else:
            counts["backfilled"] += 1

    logger.info(
        "Migration summary: backfilled=%d skipped_existing=%d failed=%d total=%d%s",
        counts["backfilled"], counts["skipped_existing"], counts["failed"],
        len(rows),
        " (dry-run)" if dry_run else "",
    )
    return 1 if counts["failed"] > 0 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + sign envelopes but skip the put_public_key write",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
