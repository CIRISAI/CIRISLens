"""
Lens-owned table retention — pressure-gated, oldest-first chunked deletes.

CIRISLens#21 surfaced ~750MB+ of unbounded growth on three lens-owned
non-hypertable tables (`manager_telemetry`, `collection_errors`,
`connectivity_events`). Schedule-blind drop-after-N-days is the wrong
shape — the right one is *backpressure-driven*: do nothing while we
have headroom; when DB size crosses a soft cap, delete just enough
oldest rows (per-table, tier-ordered) to get back under the floor.

Design:

1. **Idle by default.** The sweeper runs every 4h but only acts when
   `pg_database_size('cirislens') > TRIGGER_PCT * CAP_BYTES`. Below
   that, total no-op — we have room and shouldn't churn the DB.

2. **Tier-ordered shedding.** Policies carry a `tier` field; lower
   tiers shed first. Default: high-volume telemetry (tier 1) sheds
   before low-volume errors (tier 2).

3. **Minimum retention floor per table.** `min_keep_days` is sacred —
   rows younger than that are NEVER deleted regardless of pressure.
   This is the "we don't pre-empt the most recent N days" guarantee.

4. **Chunked + re-check between batches.** Each policy deletes the
   oldest `batch_size` rows in one statement; after each batch the
   sweeper re-checks `pg_database_size` and stops as soon as we're
   under `TARGET_PCT * CAP_BYTES`. "Delete just enough" — not the
   full retention window in one pass.

5. **VACUUM at the end of a sweep cycle.** Postgres MVCC doesn't
   free disk to the OS until VACUUM runs; a single `VACUUM` after
   the deletes finish makes the size measurement on the next cycle
   reflect what we actually did.

6. **Failure-tolerant.** Connection errors / lock contention NEVER
   kill the sweeper — log + move to the next policy; the next cycle
   picks up.

Configuration (env vars, all optional; defaults below):

  CIRISLENS_RETENTION_DB_CAP_BYTES    10 737 418 240 (10 GiB)
  CIRISLENS_RETENTION_TRIGGER_PCT     0.90  — sweep activates above this
  CIRISLENS_RETENTION_TARGET_PCT      0.80  — sweep stops when under this
  CIRISLENS_RETENTION_INTERVAL_SECS   14 400 (4h)
  CIRISLENS_RETENTION_DISABLED        unset/false — set to "1" to disable

The "contextful compaction" framing from #21 (semantic context from
dream sessions + retention heuristics deciding lossy-compression
fidelity) is upstream design — this module ships the universal
substrate (pressure-gated oldest-first drop) the higher-level
fidelity-tiered compaction composes on top of.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionPolicy:
    """Per-table retention spec, pressure-gated.

    ``schema_table`` — fully-qualified name (`cirislens.foo`).
    ``ts_column`` — timestamp column the DELETE filter targets.
    ``min_keep_days`` — sacred floor; rows younger than this are
        NEVER deleted regardless of pressure.
    ``tier`` — shedding priority (lower = sheds first under pressure).
    ``batch_size`` — rows per DELETE statement (lock-contention cap).
    """
    schema_table: str
    ts_column: str
    min_keep_days: int = 7
    tier: int = 2
    batch_size: int = 10_000


# Lens-side defaults — three policies for the three uncovered
# tables from #21. The substrate tier (trace_events / trace_llm_calls)
# is NOT here; tracked upstream as the persist `set_retention`
# primitive.
DEFAULT_POLICIES: tuple[RetentionPolicy, ...] = (
    # 7K rows/day, 757MB pre-#21 — sheds first under pressure.
    RetentionPolicy("cirislens.manager_telemetry", "collected_at", min_keep_days=7, tier=1),
    # Low rate, low volume — sheds last.
    RetentionPolicy("cirislens.connectivity_events", "occurred_at", min_keep_days=7, tier=1),
    RetentionPolicy("cirislens.collection_errors", "occurred_at", min_keep_days=14, tier=2),
)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r; using default %s", name, raw, default)
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# 10 GiB default cap — chosen so a typical lens-only deploy can run
# years without pressure. Operators override via env.
DEFAULT_CAP_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_TRIGGER_PCT = 0.90
DEFAULT_TARGET_PCT = 0.80
DEFAULT_INTERVAL_SECS = 4 * 60 * 60


@dataclass
class _SweeperState:
    """Module-local sweeper state."""
    policies: tuple[RetentionPolicy, ...] = DEFAULT_POLICIES
    task: asyncio.Task[None] | None = None
    last_sweep_summary: dict[str, Any] = field(default_factory=dict)


_state = _SweeperState()


async def _db_size_bytes(acquire_conn: Callable[[], Any]) -> int | None:
    """Return current DB size in bytes, or None if the query fails
    (caller logs + skips this cycle)."""
    try:
        async with acquire_conn() as conn:
            return int(await conn.fetchval("SELECT pg_database_size(current_database())"))
    except Exception:
        logger.exception("Retention: failed to read pg_database_size")
        return None


async def _vacuum(acquire_conn: Callable[[], Any], schema_tables: list[str]) -> None:
    """Issue VACUUM (ANALYZE) on each touched table so the next
    cycle's pg_database_size reflects the deletes. Single statement
    per table, no lock escalation — VACUUM uses MVCC, never blocks
    readers. Failures are logged + swallowed; VACUUM is opportunistic.
    """
    for table in schema_tables:
        try:
            async with acquire_conn() as conn:
                # VACUUM must run outside a transaction block. asyncpg
                # autocommits each `execute` outside an explicit
                # `async with conn.transaction()`, so this is fine.
                await conn.execute(f"VACUUM (ANALYZE) {table}")
        except Exception:
            logger.exception("Retention: VACUUM failed on %s", table)


async def _delete_oldest_batch(
    acquire_conn: Callable[[], Any],
    policy: RetentionPolicy,
) -> int:
    """Delete one batch of oldest rows from this table, respecting
    ``min_keep_days`` as the absolute floor. Returns rows deleted.

    Identifiers come from frozen ``RetentionPolicy`` (not user input);
    the timestamp filter is a literal interval. S608 silenced on the
    DELETE line only.
    """
    # identifiers come from a frozen dataclass we control, not user input
    sql = (
        f"DELETE FROM {policy.schema_table} "  # noqa: S608
        f"WHERE ctid IN ("
        f"  SELECT ctid FROM {policy.schema_table} "
        f"  WHERE {policy.ts_column} < NOW() - INTERVAL '{policy.min_keep_days} days' "
        f"  ORDER BY {policy.ts_column} "
        f"  LIMIT {policy.batch_size}"
        f")"
    )
    try:
        async with acquire_conn() as conn:
            result = await conn.execute(sql)
    except Exception as e:
        msg = str(e).lower()
        if "relation" in msg and "does not exist" in msg:
            # First deploy before the creating migration has run.
            logger.info("Retention: %s not yet created; skipping", policy.schema_table)
            return -1
        logger.exception("Retention: DELETE failed on %s", policy.schema_table)
        return -1
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        logger.warning("Retention: could not parse DELETE result %r on %s", result, policy.schema_table)
        return 0


async def sweep_once(
    acquire_conn: Callable[[], Any],
    policies: tuple[RetentionPolicy, ...] | None = None,
    cap_bytes: int | None = None,
    trigger_pct: float | None = None,
    target_pct: float | None = None,
) -> dict[str, Any]:
    """Run one pressure-gated sweep cycle.

    Returns a diagnostic dict:
        {
          "size_before_bytes": int,
          "size_after_bytes":  int,
          "cap_bytes":         int,
          "triggered":         bool,
          "deleted_per_table": {table: n_deleted},
        }
    """
    policies = policies or _state.policies
    cap_bytes = cap_bytes if cap_bytes is not None else _int_env("CIRISLENS_RETENTION_DB_CAP_BYTES", DEFAULT_CAP_BYTES)
    trigger_pct = trigger_pct if trigger_pct is not None else _float_env("CIRISLENS_RETENTION_TRIGGER_PCT", DEFAULT_TRIGGER_PCT)
    target_pct = target_pct if target_pct is not None else _float_env("CIRISLENS_RETENTION_TARGET_PCT", DEFAULT_TARGET_PCT)
    trigger_bytes = int(cap_bytes * trigger_pct)
    target_bytes = int(cap_bytes * target_pct)

    size_before = await _db_size_bytes(acquire_conn)
    if size_before is None:
        return {"size_before_bytes": None, "triggered": False, "error": "pg_database_size failed"}

    summary: dict[str, Any] = {
        "size_before_bytes": size_before,
        "cap_bytes": cap_bytes,
        "trigger_bytes": trigger_bytes,
        "target_bytes": target_bytes,
        "triggered": False,
        "deleted_per_table": {},
    }

    if size_before <= trigger_bytes:
        # Idle path — we have headroom; no work to do.
        logger.debug(
            "Retention: idle (size=%d <= trigger=%d, cap=%d)",
            size_before, trigger_bytes, cap_bytes,
        )
        summary["size_after_bytes"] = size_before
        return summary

    summary["triggered"] = True
    logger.warning(
        "Retention: PRESSURE — size=%d > trigger=%d (%.1f%% of cap=%d); sweeping to target=%d",
        size_before, trigger_bytes, 100.0 * size_before / cap_bytes, cap_bytes, target_bytes,
    )

    # Tier-ordered: lower tiers shed first.
    ordered = sorted(policies, key=lambda p: (p.tier, p.schema_table))
    touched: list[str] = []
    current_size = size_before
    cap_reached = False

    for policy in ordered:
        if cap_reached:
            break
        per_table_total = 0
        # Per-policy safety cap: don't run more than this many batches
        # for one table per sweep — gives every tier a chance to
        # contribute. With batch_size=10k that's 1M rows per table
        # per cycle.
        for _ in range(100):
            deleted = await _delete_oldest_batch(acquire_conn, policy)
            if deleted < 0:
                # Error / missing table — skip the rest of this policy.
                break
            per_table_total += deleted
            if deleted < policy.batch_size:
                # Window drained — all rows older than min_keep_days are gone.
                break
            # Yield + re-check pressure mid-policy.
            await asyncio.sleep(0.05)
            new_size = await _db_size_bytes(acquire_conn)
            if new_size is not None:
                current_size = new_size
                if current_size <= target_bytes:
                    cap_reached = True
                    break
        if per_table_total > 0:
            touched.append(policy.schema_table)
            summary["deleted_per_table"][policy.schema_table] = per_table_total
            logger.info(
                "Retention: %s shed %d rows (tier=%d, min_keep=%dd)",
                policy.schema_table, per_table_total, policy.tier, policy.min_keep_days,
            )

    # VACUUM the touched tables so the next cycle sees accurate sizes.
    if touched:
        await _vacuum(acquire_conn, touched)

    size_after = await _db_size_bytes(acquire_conn)
    summary["size_after_bytes"] = size_after
    if size_after is not None and size_after > target_bytes:
        logger.warning(
            "Retention: sweep finished with size=%d STILL ABOVE target=%d "
            "— policies exhausted at min_keep_days floor; consider lowering "
            "min_keep_days or raising the cap.",
            size_after, target_bytes,
        )
    return summary


async def _run_sweeper(acquire_conn: Callable[[], Any]) -> None:
    """Background sweeper — runs forever until cancelled."""
    initial_delay = 60  # let startup settle
    interval = _int_env("CIRISLENS_RETENTION_INTERVAL_SECS", DEFAULT_INTERVAL_SECS)
    await asyncio.sleep(initial_delay)
    logger.info(
        "Retention sweeper starting: %d policies, interval=%ds",
        len(_state.policies), interval,
    )
    while True:
        try:
            _state.last_sweep_summary = await sweep_once(acquire_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention sweep cycle failed")
        await asyncio.sleep(interval)


def start_retention_sweeper(acquire_conn: Callable[[], Any]) -> None:
    """Start the background sweeper. Idempotent.

    Set ``CIRISLENS_RETENTION_DISABLED=1`` to skip (for dev/test
    environments where retention churn would interfere with
    fixtures).
    """
    if _bool_env("CIRISLENS_RETENTION_DISABLED"):
        logger.info("Retention sweeper disabled via CIRISLENS_RETENTION_DISABLED")
        return
    if _state.task is not None and not _state.task.done():
        return
    _state.task = asyncio.create_task(_run_sweeper(acquire_conn))


def stop_retention_sweeper() -> None:
    """Stop the sweeper. Idempotent."""
    if _state.task is not None and not _state.task.done():
        _state.task.cancel()
        _state.task = None


def get_last_sweep_summary() -> dict[str, Any]:
    """Diagnostic accessor — returns the last sweep's full summary
    (pressure measurements + per-table counts). Empty dict before
    the first sweep completes."""
    return dict(_state.last_sweep_summary)
