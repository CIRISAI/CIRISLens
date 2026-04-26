"""
R3.4 — Parallel-run scrubber comparison harness.

Runs both v1 (Python spaCy) and v2 (Rust core) on the same trace, reports
divergences. Designed to run in shadow mode during the migration window
(FSD §8 Stage 3): v1 result is what gets persisted; v2 output is observed
only.

Usage from the trace handler:

    from api.scrubber_compare import compare_and_persist

    # Returns the v1-scrubbed dict (still the persistence source-of-truth).
    # Divergence records are emitted to the configured sink as a side effect.
    scrubbed = compare_and_persist(trace_dict, level)
    if scrubbed is None:
        # v1 itself failed — reject the trace.
        raise SomeRejectionError(...)
    # ... persist `scrubbed` ...

Outputs a JSONL stream of divergence records to a configurable sink (file
path or stdout). Each record:

    {
      "ts":          "ISO timestamp",
      "trace_id":    "...",
      "level":       "detailed" | "full_traces",
      "shape":       "v1_only" | "v2_only" | "both" | "neither",
      "v1_status":   "ok" | "error: <msg>",
      "v2_status":   "ok" | "error: <msg>" | "not_configured",
      "v1_changed":  bool,            # did v1 modify the trace
      "v2_changed":  bool,
      "value_eq":    bool,            # bytewise-equal scrubbed values
      "fields_diff": [path, ...]      # JSON paths where the values differ
    }

The classification (R3.5 — improvement / regression / equivalent) is a
separate offline step that consumes this JSONL stream.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from api import scrubber_v2 as v2

# v1 path is optional in non-package contexts (e.g., running this file
# standalone with PYTHONPATH=.); the package import is the normal route.
try:
    from api import pii_scrubber as v1
except ImportError:  # pragma: no cover
    import pii_scrubber as v1  # type: ignore

logger = logging.getLogger(__name__)

DivergenceRecord = dict[str, Any]


def _diff_paths(a: Any, b: Any, path: str = "") -> list[str]:
    """Return JSON paths where `a` differs from `b`. Bounded depth to
    avoid pathological dicts."""
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        diffs: list[str] = []
        for key in set(a) | set(b):
            sub = f"{path}.{key}" if path else key
            if key not in a or key not in b:
                diffs.append(sub)
            else:
                diffs.extend(_diff_paths(a[key], b[key], sub))
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}[len {len(a)}!={len(b)}]"]
        out: list[str] = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out.extend(_diff_paths(x, y, f"{path}[{i}]"))
        return out
    return [path or "<root>"]


def _run_v1(trace: dict[str, Any], _level: str) -> tuple[dict[str, Any] | None, str]:
    """Run v1 scrubber. Returns (scrubbed_or_none, status_string).

    `_level` is unused — v1's `scrub_dict_recursive` is level-agnostic —
    but the parameter is kept to mirror `_run_v2` for symmetry at the
    call site.
    """
    try:
        scrubbed = v1.scrub_dict_recursive(trace)
        return scrubbed, "ok"
    except Exception as e:
        return None, f"error: {e}"


def _run_v2(trace: dict[str, Any], level: str) -> tuple[dict[str, Any] | None, str]:
    """Run v2 scrubber. Returns (scrubbed_or_none, status_string)."""
    if not v2.is_available():
        return None, "not_configured"
    if level == "full_traces" and not v2.ner_is_configured():
        return None, "not_configured"
    try:
        result = v2.scrub_for_persistence(trace, level)
        return result.trace, "ok"
    except v2.ScrubError as e:
        return None, f"error: {e}"
    except ValueError as e:
        return None, f"error: {e}"


def compare(
    trace: dict[str, Any],
    level: str,
    *,
    trace_id: str | None = None,
) -> tuple[dict[str, Any] | None, DivergenceRecord]:
    """Run v1 and v2 in parallel; return (v1_output, divergence_record).

    Caller persists v1_output (the v1 result remains the source of truth
    during the migration window). The divergence_record is logged for
    offline classification.

    Returns `(None, record)` if v1 itself failed — caller must reject the
    trace, since v1 is currently the persistence path.
    """
    v1_value, v1_status = _run_v1(trace, level)
    v2_value, v2_status = _run_v2(trace, level)

    v1_changed = v1_value is not None and v1_value != trace
    v2_changed = v2_value is not None and v2_value != trace

    if v1_value is None and v2_value is None:
        shape = "neither"
        value_eq = True
    elif v1_value is None:
        shape = "v2_only"
        value_eq = False
    elif v2_value is None:
        shape = "v1_only"
        value_eq = False
    else:
        shape = "both"
        value_eq = v1_value == v2_value

    fields_diff: list[str] = []
    if shape == "both" and not value_eq:
        fields_diff = _diff_paths(v1_value, v2_value)[:32]  # cap output

    record: DivergenceRecord = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "trace_id": trace_id,
        "level": level,
        "shape": shape,
        "v1_status": v1_status,
        "v2_status": v2_status,
        "v1_changed": v1_changed,
        "v2_changed": v2_changed,
        "value_eq": value_eq,
        "fields_diff": fields_diff,
    }
    return v1_value, record


# ── Sink for divergence records ──
#
# `functools.cache` gives us a process-lifetime singleton without a `global`
# statement. The sink is intentionally never closed: stderr/stdout don't
# need it, and a path-backed sink should outlive any single trace handler.


@functools.cache
def _sink() -> IO[str]:
    """Resolve the divergence sink once. Configurable via
    `CIRISLENS_SCRUBBER_DIVERGENCE_LOG` (path or `stderr` / `stdout`)."""
    target = os.environ.get("CIRISLENS_SCRUBBER_DIVERGENCE_LOG", "stderr")
    if target == "stderr":
        return sys.stderr
    if target == "stdout":
        return sys.stdout
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def log_divergence(record: DivergenceRecord) -> None:
    """Emit a divergence record to the configured sink."""
    try:
        _sink().write(json.dumps(record, ensure_ascii=False) + "\n")
        _sink().flush()
    except Exception as e:
        logger.warning("divergence sink write failed: %s", e)


def compare_and_persist(
    trace: dict[str, Any],
    level: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Convenience entry point for the trace handler. Runs both scrubbers,
    logs the divergence, returns the v1 result (still the persistence
    source-of-truth during the migration window).

    Returns None if v1 failed — caller MUST reject the trace.
    """
    v1_value, record = compare(trace, level, trace_id=trace_id)

    # Filter rules for the divergence sink:
    #   - both ran + agreed: nothing to log (the happy path).
    #   - v2 not_configured: expected during the rollout window, not a
    #     divergence to investigate. Skip so we don't drown the sink.
    #   - both ran + disagreed: log (this is the real signal R3.5
    #     classifies).
    #   - v1 errored / v2 errored: log (always interesting).
    skip = (
        (record["shape"] == "both" and record["value_eq"])
        or record["v2_status"] == "not_configured"
    )
    if not skip:
        log_divergence(record)

    return v1_value


# ── Standalone CLI for replaying a corpus through both scrubbers ──

def _main_cli() -> int:
    """python -m api.scrubber_compare < traces.jsonl > divergences.jsonl

    Each input line: {"trace": {...}, "level": "...", "trace_id": "..."}
    Each output line: divergence record.
    """
    p = argparse.ArgumentParser(description="Replay corpus through v1+v2 comparison")
    p.add_argument("--in", dest="input", default="-",
                   help="JSONL input (- = stdin)")
    p.add_argument("--out", dest="output", default="-",
                   help="JSONL divergence output (- = stdout)")
    args = p.parse_args()

    n = divergent = 0
    with contextlib.ExitStack() as stack:
        in_fh: IO[str] = (
            sys.stdin if args.input == "-"
            else stack.enter_context(Path(args.input).open(encoding="utf-8"))
        )
        out_fh: IO[str] = (
            sys.stdout if args.output == "-"
            else stack.enter_context(Path(args.output).open("w", encoding="utf-8"))
        )
        for line in in_fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            trace = row.get("trace") or row
            level = row.get("level") or row.get("trace_level") or "detailed"
            tid = row.get("trace_id")
            _, record = compare(trace, level, trace_id=tid)
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
            if record["shape"] != "both" or not record["value_eq"]:
                divergent += 1

    msg = f"compared: {n}  divergent: {divergent} ({divergent/n*100:.1f}%)" if n else "no input"
    print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main_cli())
