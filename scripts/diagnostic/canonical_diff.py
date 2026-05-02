"""
Offline canonicalization diff — for any captured wire body, compute:
  (1) what persist v0.1.18 produces for both 9-field and 2-field shapes
  (2) what the agent SHOULD produce per the wire-format spec
  (3) what the agent's actual signing code produces (per
      CIRISAgent/ciris_adapters/ciris_accord_metrics/services.py — 2-field)

Diff (1) vs (3) byte-for-byte: if persist's 2-field bytes match the
agent's signing-code 2-field bytes, persist canonicalizer is correct
and verify should pass. If they don't match, that's the drift to fix.
"""
import sys, json, base64, hashlib, asyncio
from pathlib import Path
sys.path.insert(0, "api")

import ciris_persist as cp
import persist_engine


def strip_empty(o):
    """Match agent + lens-legacy strip_empty semantics exactly."""
    if isinstance(o, dict):
        return {k: strip_empty(v) for k, v in o.items()
                if not (v is None or v == "" or v == [] or v == {})}
    if isinstance(o, list):
        return [strip_empty(x) for x in o]
    return o


def agent_canonical_2field(trace: dict) -> bytes:
    """What CIRISAgent ciris_accord_metrics/services.py::sign_trace
    actually signs over (the 2-field, lens-legacy-compat shape)."""
    components = [
        strip_empty({
            "component_type": c["component_type"],
            "data":           c.get("data", {}),
            "event_type":     c["event_type"],
            "timestamp":      c["timestamp"],
        })
        for c in trace["components"]
    ]
    canonical = {
        "components": components,
        "trace_level": trace["trace_level"],
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()


def agent_canonical_9field(trace: dict) -> bytes:
    """What TRACE_WIRE_FORMAT.md §8 spec describes (9-field)."""
    components = [
        {
            "component_type": c["component_type"],
            "data":           c.get("data", {}),
            "event_type":     c["event_type"],
            "timestamp":      c["timestamp"],
        }
        for c in trace["components"]
    ]
    canonical = {
        "trace_id":             trace["trace_id"],
        "thought_id":           trace["thought_id"],
        "task_id":              trace["task_id"],
        "agent_id_hash":        trace["agent_id_hash"],
        "started_at":           trace["started_at"],
        "completed_at":         trace["completed_at"],
        "trace_level":          trace["trace_level"],
        "trace_schema_version": trace["trace_schema_version"],
        "components":           components,
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()


async def diff_one(body_path: Path):
    body = body_path.read_bytes()
    print(f"\n{'='*72}")
    print(f"  {body_path.name}")
    print(f"{'='*72}")
    print(f"body sha256 prefix: {hashlib.sha256(body).hexdigest()[:16]}")

    # Get persist's perspective via debug_canonicalize
    engine = persist_engine.get_engine()
    try:
        persist_results = engine.debug_canonicalize(body)
    except Exception as e:
        print(f"engine.debug_canonicalize raised: {type(e).__name__}: {e}")
        return

    if not persist_results:
        print("  persist returned no results")
        return

    # Should be a list (one per event) — take the first
    pr = persist_results[0] if isinstance(persist_results, list) else persist_results
    print(f"  trace_id:               {pr.get('trace_id', '<missing>')}")
    print(f"  signature_key_id:       {pr.get('signature_key_id', '<missing>')}")

    # Agent-side reference
    data = json.loads(body)
    trace = data["events"][0]["trace"]
    sig_b64 = trace["signature"]
    sig_url = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

    py_2f = agent_canonical_2field(trace)
    py_9f = agent_canonical_9field(trace)

    # Pull persist's bytes
    p_2f_b64 = pr.get("canonical_2field_b64")
    p_9f_b64 = pr.get("canonical_9field_b64")
    p_2f = base64.b64decode(p_2f_b64) if p_2f_b64 else None
    p_9f = base64.b64decode(p_9f_b64) if p_9f_b64 else None

    print()
    print(f"{'shape':<12} {'python_sha':<18} {'persist_sha':<18} {'len_py':<8} {'len_p':<8} match")
    for name, py, p in [("2-field", py_2f, p_2f), ("9-field", py_9f, p_9f)]:
        py_h = hashlib.sha256(py).hexdigest()[:16] if py else "?"
        p_h  = hashlib.sha256(p).hexdigest()[:16] if p else "?"
        match = "✓" if py == p else "✗"
        ply = len(py) if py else 0
        plen = len(p) if p else 0
        print(f"{name:<12} {py_h:<18} {p_h:<18} {ply:<8} {plen:<8} {match}")

    # If persist's 2-field doesn't match python's 2-field, find first diff
    for name, py, p in [("2-field", py_2f, p_2f), ("9-field", py_9f, p_9f)]:
        if py == p or p is None:
            continue
        for i, (a, b) in enumerate(zip(py, p)):
            if a != b:
                ctx = 25
                print()
                print(f"  {name}: first diverging byte at offset {i}:")
                print(f"    python:   ...{py[max(0,i-ctx):min(len(py),i+ctx*2)]}")
                print(f"    persist:  ...{p[max(0,i-ctx):min(len(p),i+ctx*2)]}")
                break
        else:
            print(f"\n  {name}: one is a prefix of the other; len diff {abs(len(py)-len(p))}")


async def main():
    await persist_engine.initialize()
    for f in sorted(Path("diagnostic/raw_bodies").glob("*YO-REJECTED*.json")):
        await diff_one(f)


asyncio.run(main())
