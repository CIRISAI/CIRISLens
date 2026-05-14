"""
Scrubber v2 — thin Python wrapper around `cirislens_core.scrub_trace`.

Per FSD `CIRIS_SCRUBBING_V2.md`, the Rust scrubber is the only path to
persistence for trace text content. This module is the call-site bridge:

- `scrub_for_persistence(trace, level)` runs the Rust scrubber and returns
  the scrubbed dict. Caller MUST consume the return value and never
  reference the input again before persisting — that's the invariant the
  FSD encodes (Rust ownership prevents pre-scrub writes inside the core;
  Python callers enforce it by discipline).

- `should_use_v2(level)` returns True when the Rust scrubber is available
  and configured for the level. During the migration window the trace
  handler can call this to decide whether to dispatch to v2 or fall back
  to the v1 Python scrubber.

The migration path is documented in FSD §8 Critical path Stages 2-4.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Try to import the Rust core. v0.1.1 cutover: package is now
# `ciris-lens-core` on PyPI (module `ciris_lens_core`); aliased to
# `cirislens_core` to keep call sites stable through the migration.
# On missing install or NER backend not configured, this falls back to
# indicating v2 is unavailable; the caller should use v1.
try:
    import ciris_lens_core as cirislens_core  # type: ignore[import-not-found]

    _RUST_AVAILABLE = hasattr(cirislens_core, "scrub_trace")
    if _RUST_AVAILABLE:
        logger.info("Scrubber v2 (Rust core) loaded")
        # Eager-load the NER backend at module import. Without this the
        # backend stays lazy until the first `full_traces` request — meaning
        # a misconfigured ort path (missing model, wrong ORT_DYLIB_PATH,
        # incompatible onnxruntime) only surfaces on a real production
        # POST. Forcing the load at startup gives the boot log the
        # `[cirislens_core] NER backend ready (...)` signature operators
        # rely on for deploy verification, and the orchestrator can roll
        # back a broken image before traffic ever hits it.
        try:
            ner_ready = cirislens_core.ner_is_configured()
            logger.info("Scrubber v2 NER backend eager-load: ner_ready=%s", ner_ready)
        except Exception as e:
            # Don't kill the worker on NER-init failure: detailed traces
            # still scrub via the regex path. Log loud and keep going so
            # the partial-coverage state is visible in operator dashboards
            # rather than producing a hard boot failure.
            logger.warning("Scrubber v2 NER eager-load raised: %s", e)
    else:
        logger.warning(
            "cirislens_core present but scrub_trace not exposed — rebuild required"
        )
except ImportError:
    cirislens_core = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False
    logger.warning("cirislens_core not built; scrubber v2 unavailable, falling back to v1")


class ScrubError(RuntimeError):
    """Raised when scrubbing fails. The trace MUST NOT be persisted."""


def is_available() -> bool:
    """True iff the Rust scrubber is loaded. Returns False during the
    migration window when the Rust core hasn't been built locally yet."""
    return _RUST_AVAILABLE


def ner_is_configured() -> bool:
    """True iff the Rust NER backend is loaded and ready (model + tokenizer
    files present, ort feature compiled in). When False, full_traces traces
    must use the v1 Python scrubber or be rejected."""
    if not _RUST_AVAILABLE:
        return False
    try:
        return bool(cirislens_core.ner_is_configured())
    except Exception as e:
        logger.warning("ner_is_configured() raised: %s", e)
        return False


def should_use_v2(level: str) -> bool:
    """Decide whether to dispatch to the Rust scrubber for this trace level.

    Operator override via `CIRISLENS_SCRUBBER_VERSION=v1` forces the legacy
    Python path. Otherwise:
      - generic traces: always v2 (no NER needed; pure pass-through in v2)
      - detailed traces: v2 when Rust core is loaded
      - full_traces:    v2 only when NER backend is configured
    """
    if os.environ.get("CIRISLENS_SCRUBBER_VERSION") == "v1":
        return False
    if not _RUST_AVAILABLE:
        return False
    if level in {"generic", "detailed"}:
        return True
    if level == "full_traces":
        return ner_is_configured()
    return False


class ScrubbedTrace:
    """Type-tagged wrapper around a scrubbed trace dict.

    R2.3 in the FSD calls for the persistence layer to require this
    type rather than raw `dict`, so that "persist a trace that hasn't
    been through the scrubber" fails type-checking. Python's dynamic
    typing means this is convention rather than compile-time enforcement,
    but mypy strict mode catches it.

    The class is intentionally inert — no methods beyond access. It's a
    nominal type, not a behavioral one. The point is that you can't
    construct one without going through `scrub_for_persistence`.
    """

    __slots__ = ("_level", "_stats", "_trace")

    def __init__(
        self,
        _trace: dict[str, Any],
        _level: str,
        _stats: dict[str, Any],
        *,
        _internal_token: object | None = None,
    ) -> None:
        # The internal token gates direct construction. Code outside this
        # module has no way to instantiate ScrubbedTrace except via the
        # `scrub_for_persistence` path. Linters may complain; that's fine.
        if _internal_token is not _CONSTRUCT_TOKEN:
            raise TypeError(
                "ScrubbedTrace cannot be instantiated directly. "
                "Use scrubber_v2.scrub_for_persistence() to obtain one."
            )
        self._trace = _trace
        self._level = _level
        self._stats = _stats

    @property
    def trace(self) -> dict[str, Any]:
        """The scrubbed trace dict. Safe for persistence."""
        return self._trace

    @property
    def level(self) -> str:
        return self._level

    @property
    def stats(self) -> dict[str, Any]:
        return self._stats

    def __repr__(self) -> str:
        return f"ScrubbedTrace(level={self._level!r}, stats={self._stats!r})"


# Module-private sentinel — only this module can pass it to ScrubbedTrace.
_CONSTRUCT_TOKEN = object()


def scrub_for_persistence(trace: dict[str, Any], level: str) -> ScrubbedTrace:
    """Scrub a trace via the Rust core. Returns a `ScrubbedTrace` whose
    `.trace` property is the only value the persistence layer should consume.

    The input `trace` dict MUST NOT be referenced after this call returns.
    Any exception means the trace was not scrubbed and must be rejected
    per FSD §6 (no partial-scrub persistence).

    Raises:
        ScrubError: scrubbing failed (NER not configured, walker depth,
                    year residue, operator probe match — message has detail)
        ValueError: invalid level string
    """
    if not _RUST_AVAILABLE:
        raise ScrubError(
            "Rust scrubber not available; the trace handler should fall "
            "back to the v1 Python scrubber via `should_use_v2(level)` "
            "before calling this function"
        )

    trace_json = json.dumps(trace, ensure_ascii=False)
    try:
        result = cirislens_core.scrub_trace(trace_json, level)
    except ValueError:
        # Invalid level — re-raise so caller can distinguish from ScrubError
        raise
    except RuntimeError as e:
        # All other scrub failures (NerNotConfigured, depth, residue, probe)
        raise ScrubError(str(e)) from e

    scrubbed_trace = json.loads(result["trace"])
    stats: dict[str, Any] = dict(result["stats"])
    return ScrubbedTrace(
        _trace=scrubbed_trace,
        _level=level,
        _stats=stats,
        _internal_token=_CONSTRUCT_TOKEN,
    )
