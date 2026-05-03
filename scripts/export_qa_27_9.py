#!/usr/bin/env python3
"""
Export post-2.7.9 QA-eval traffic for the RATCHET validation corpus
(closes CIRISLens#4).

Pulls trace_events + trace_llm_calls + accord_public_keys filtered to:
  - schema_version = '2.7.9'                   (locked wire format)
  - trace_level IN ('detailed', 'full_traces')  (generic is content-free)
  - QA traffic                                  (THOUGHT_START.channel_id LIKE 'model_eval_%')

Reads via the v0.3.2 read-only role (cirislens_reader / CIRISLens#9
+ CIRISPersist#9) using `CIRISLENS_READ_DSN`. Writes one JSONL file
per table + a MANIFEST.json with sha256 + row counts + filter
parameters + export timestamp.

Streams via asyncpg cursor — doesn't load entire result sets in memory,
so a quarterly export of millions of rows runs in bounded memory.

## Usage

The export script is operator-driven (manual or scheduled). Typical:

    CIRISLENS_READ_DSN=postgres://cirislens_analytics:...@db/cirislens \
        python scripts/export_qa_27_9.py \
            --output-dir release_v2/data/2026-05-03T00-00-00Z/

Optional time-windowing (defaults to "all 2.7.9 traffic ever"):

    --since 2026-05-01T00:00:00Z
    --until 2026-12-31T23:59:59Z

## Why not write to release_v2/ directly

The output dir is a parameter, not a fixed path, because this script
is run both:
  - by humans for ad-hoc exports / sanity checks
  - by CI on a quarterly cron writing to a timestamped subdir under
    release_v2/data/ before pushing to HuggingFace

## Provenance preservation

Signed bytes (the `signature` + `signing_key_id` columns on
trace_events) are preserved verbatim in the JSONL output. RATCHET
re-verifies provenance against the lens-steward chain by:
  1. Resolving signing_key_id → public_key_base64 via the included
     accord_public_keys.jsonl
  2. Recomputing canonical bytes from the row's trace_level + payload
     per the wire-format spec at CIRISAgent/FSD/TRACE_WIRE_FORMAT.md
     @ v2.7.9-stable
  3. Verifying Ed25519 sig against the public key

The script does NOT re-canonicalize on export — keeping persist's
canonicalization as authoritative (CIRISPersist#7 lesson: byte-stable
crypto behavior belongs in one place; consumers don't reimplement).

## Schema columns published

Columns emitted match `PUBLIC_SCHEMA_CONTRACT.md` `stable` + `stable-ro`
tier, plus `internal` audit columns since RATCHET wants signed-bytes
preservation. Internal columns may move or change at a future persist
minor; consumers SHOULD treat them as best-effort.

## Output layout

    <output-dir>/
      trace_events.jsonl         — one JSON object per row, ORDER BY trace_id, ts
      trace_llm_calls.jsonl      — one JSON object per row, ORDER BY trace_id, ts
      accord_public_keys.jsonl   — one JSON object per row, ORDER BY created_at
      MANIFEST.json              — sha256 + row counts + filter params

Exit codes:
  0 = clean export
  1 = at least one phase failed (manifest will be missing or partial)
  2 = pre-flight failed (no read DSN, DB unreachable, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger("export_qa_27_9")

# Single source of truth for the filter predicate. Injected into the
# qa_traces CTE used by both trace_events and trace_llm_calls extraction
# so they share exactly one definition of "what counts as a qa_eval
# trace at v2.7.9 with detailed-or-better content".
_QA_TRACES_CTE = """
qa_traces AS (
    SELECT DISTINCT trace_id
    FROM cirislens.trace_events
    WHERE schema_version = '2.7.9'
      AND event_type = 'THOUGHT_START'
      AND payload->>'channel_id' LIKE 'model_eval_%'
      AND trace_level IN ('detailed', 'full_traces')
      AND ($1::timestamptz IS NULL OR ts >= $1)
      AND ($2::timestamptz IS NULL OR ts <= $2)
)
"""


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _parse_iso(s: str | None) -> dt.datetime | None:
    """Parse ISO 8601 → tz-aware datetime, or None when input is None."""
    if s is None:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _json_default(obj: Any) -> Any:
    """asyncpg returns datetime, Decimal, bytes — make them JSON-safe.

    JSONB columns come back as already-parsed dicts/lists (asyncpg has
    a built-in jsonb codec) so they don't need special handling here.
    """
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Unserializable: {type(obj).__name__}")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _stream_query_to_jsonl(
    *,
    conn: asyncpg.Connection,
    sql: str,
    params: tuple,
    out_path: Path,
    label: str,
) -> int:
    """Run a SELECT, stream rows to JSONL via a server-side cursor.

    Cursor avoids materializing the full result set in memory or in the
    asyncpg client buffer; the export scales to millions of rows in
    bounded memory. Returns the number of rows written.

    Each line is `json.dumps(dict(row), separators=(",",":"), sort_keys=True)`
    so the output is byte-stable across reorderings of the underlying
    SELECT — RATCHET can sha256-compare two exports without false diff
    on key order.
    """
    written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with conn.transaction():  # cursor requires an open transaction
        cur = conn.cursor(sql, *params, prefetch=1000)
        with out_path.open("w", encoding="utf-8") as f:
            async for row in cur:
                obj = dict(row)
                f.write(json.dumps(
                    obj,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=_json_default,
                    ensure_ascii=False,
                ))
                f.write("\n")
                written += 1
                if written % 10_000 == 0:
                    logger.info("  %s: %d rows...", label, written)
    logger.info("  %s: %d rows -> %s", label, written, out_path)
    return written


async def _export_trace_events(
    conn: asyncpg.Connection, since: dt.datetime | None,
    until: dt.datetime | None, out_dir: Path,
) -> int:
    """All trace_events for traces in the qa_traces CTE."""
    # _QA_TRACES_CTE is a module-level constant with no caller-controlled
    # interpolation; the only $1/$2 binds are real query parameters
    # passed via asyncpg's prepared-statement path. ruff's S608 doesn't
    # track that.
    sql = f"""
    WITH {_QA_TRACES_CTE}
    SELECT te.*
    FROM cirislens.trace_events te
    JOIN qa_traces q USING (trace_id)
    ORDER BY te.trace_id, te.ts, te.event_id
    """  # noqa: S608
    return await _stream_query_to_jsonl(
        conn=conn, sql=sql, params=(since, until),
        out_path=out_dir / "trace_events.jsonl",
        label="trace_events",
    )


async def _export_trace_llm_calls(
    conn: asyncpg.Connection, since: dt.datetime | None,
    until: dt.datetime | None, out_dir: Path,
) -> int:
    """All trace_llm_calls for traces in the qa_traces CTE.

    Joined on trace_id (the v2.7.9 wire format also exposes
    parent_event_type + parent_attempt_index for the structural FK,
    but trace_id alone is sufficient for the export filter — the FK
    columns are emitted on every row so RATCHET can join client-side).
    """
    # See _export_trace_events for the noqa rationale.
    sql = f"""
    WITH {_QA_TRACES_CTE}
    SELECT lc.*
    FROM cirislens.trace_llm_calls lc
    JOIN qa_traces q USING (trace_id)
    ORDER BY lc.trace_id, lc.ts, lc.call_id
    """  # noqa: S608
    return await _stream_query_to_jsonl(
        conn=conn, sql=sql, params=(since, until),
        out_path=out_dir / "trace_llm_calls.jsonl",
        label="trace_llm_calls",
    )


async def _export_public_keys(
    conn: asyncpg.Connection, out_dir: Path,
) -> int:
    """All accord_public_keys for signature_key_id resolution.

    No time filter — RATCHET needs the full key directory to verify any
    historical row's signature (a key registered today might still be
    in scope if the trace it signed is in the windowed export). Cheap
    table; ~thousands of rows max.
    """
    sql = """
    SELECT * FROM cirislens.accord_public_keys
    ORDER BY created_at
    """
    out_path = out_dir / "accord_public_keys.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    rows = await conn.fetch(sql)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(
                dict(row), separators=(",", ":"),
                sort_keys=True, default=_json_default,
                ensure_ascii=False,
            ))
            f.write("\n")
            written += 1
    logger.info("  accord_public_keys: %d rows -> %s", written, out_path)
    return written


def _write_manifest(
    *, out_dir: Path, counts: dict[str, int],
    since: dt.datetime | None, until: dt.datetime | None,
    started_at: str, finished_at: str,
) -> Path:
    manifest_path = out_dir / "MANIFEST.json"
    files = {}
    for fname in ("trace_events.jsonl", "trace_llm_calls.jsonl", "accord_public_keys.jsonl"):
        p = out_dir / fname
        if p.exists():
            files[fname] = {
                "rows": counts.get(fname.removesuffix(".jsonl"), 0),
                "bytes": p.stat().st_size,
                "sha256": _sha256_file(p),
            }
    manifest = {
        "schema_version": "2.7.9",
        "filter": {
            "trace_schema_version": "2.7.9",
            "trace_levels": ["detailed", "full_traces"],
            "channel_id_pattern": "model_eval_%",
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "wire_spec_pin": "CIRISAgent/FSD/TRACE_WIRE_FORMAT.md @ v2.7.9-stable",
        "persist_schema_contract": "CIRISPersist/docs/PUBLIC_SCHEMA_CONTRACT.md @ v0.3.2",
        "started_at": started_at,
        "finished_at": finished_at,
        "files": files,
    }
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    logger.info("MANIFEST.json -> %s", manifest_path)
    return manifest_path


async def main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dsn = os.getenv("CIRISLENS_READ_DSN")
    if not dsn:
        logger.error(
            "CIRISLENS_READ_DSN unset. Provision a login user with "
            "GRANT cirislens_reader (per CIRISPersist v0.3.2 V005 + "
            "PUBLIC_SCHEMA_CONTRACT.md) and set CIRISLENS_READ_DSN to "
            "the login DSN before running this script.",
        )
        return 2

    since = _parse_iso(args.since)
    until = _parse_iso(args.until)
    # ASYNC240 flags the resolve() as blocking I/O; for a one-shot CLI
    # script that isn't serving requests, this runs once at startup
    # before any DB work — the event-loop-blocking concern doesn't apply.
    out_dir = Path(args.output_dir).resolve()  # noqa: ASYNC240

    logger.info("Export starting: out_dir=%s since=%s until=%s",
                out_dir, since, until)

    started_at = _utc_now_iso()
    conn = await asyncpg.connect(dsn)
    try:
        counts: dict[str, int] = {}
        try:
            counts["trace_events"] = await _export_trace_events(
                conn, since, until, out_dir,
            )
            counts["trace_llm_calls"] = await _export_trace_llm_calls(
                conn, since, until, out_dir,
            )
            counts["accord_public_keys"] = await _export_public_keys(
                conn, out_dir,
            )
        except Exception:
            logger.exception("Export phase failed")
            return 1
    finally:
        await conn.close()

    finished_at = _utc_now_iso()
    _write_manifest(
        out_dir=out_dir, counts=counts,
        since=since, until=until,
        started_at=started_at, finished_at=finished_at,
    )

    logger.info(
        "Export complete: trace_events=%d trace_llm_calls=%d accord_public_keys=%d",
        counts.get("trace_events", 0),
        counts.get("trace_llm_calls", 0),
        counts.get("accord_public_keys", 0),
    )
    return 0


def _argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write JSONL + MANIFEST.json (created if missing)",
    )
    p.add_argument(
        "--since",
        default=None,
        help="ISO 8601 lower bound on ts (e.g. 2026-05-01T00:00:00Z)",
    )
    p.add_argument(
        "--until",
        default=None,
        help="ISO 8601 upper bound on ts; defaults to now",
    )
    return p


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_argparser().parse_args())))
