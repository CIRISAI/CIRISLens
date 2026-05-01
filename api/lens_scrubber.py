"""
Lens-side scrubber adapter for ciris-persist's Engine.

Persist's PyO3 `Engine(scrubber=callable)` parameter accepts a Python
callable that receives the full BatchEnvelope dict (parsed +
signature-verified by persist) and returns
`(scrubbed_envelope_dict, modified_field_count)`. Persist enforces
schema-preservation rules on the return value — `trace_schema_version`
and `trace_level` must not change, `events[]` length and discriminants
must not change, only payload `data` mutations are permitted.

This module wraps the lens's existing two-stage pipeline:

1. **PII scrub** via `scrubber_v2.scrub_for_persistence` (Rust core,
   `cirislens_core.scrub_trace`) when available, falling back to the
   Python `pii_scrubber.scrub_dict_recursive` path. NER + regex at
   `full_traces`; regex-only at `detailed`.
2. **Security sanitize** via `security_sanitizer.sanitize_trace_for_storage`
   — neutralizes XSS / SQL-injection / command-injection patterns in
   trace content before it lands in JSONB.

Persist bypasses the callback entirely at `trace_level=generic`
(content-free traces by design — no PII to scrub, nothing to
sanitize). The adapter still defensively checks the level and
short-circuits, in case the bypass policy changes upstream.

## Failure mode

Per FSD `CIRIS_PERSIST.md` §6 + `pii_scrubber.py` invariant: scrubber
errors must reject the batch, not return partial results. The adapter
raises `ValueError` on any per-trace scrubber failure, which persist's
PyCallableScrubber bridges to `ScrubError::External` → HTTP 422 at the
lens handler boundary. PII content is never included in the error
message — only the `trace_id` and an error-class token.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pii_scrubber
import scrubber_v2
import security_sanitizer

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def make_persist_scrubber() -> Callable[[dict[str, Any]], tuple[dict[str, Any], int]]:
    """Build a persist-Engine-compatible scrubber callback.

    Returns a callable matching persist's expected signature:
    `(envelope: dict) -> (scrubbed_envelope: dict, modified_count: int)`.

    Constructed once at lens startup (`persist_engine.initialize()`).
    The callable is shared across all worker requests; it holds no
    request-scoped state.
    """

    def scrub_envelope(envelope: dict[str, Any]) -> tuple[dict[str, Any], int]:
        level = envelope.get("trace_level", "generic")

        # Generic-tier defensive bypass. Persist already short-circuits
        # at this level per CIRISPersist `PyCallableScrubber::scrub_batch`,
        # but the contract is "callable MAY be invoked at any level" —
        # honor the level invariant here rather than relying on caller.
        if level == "generic":
            return envelope, 0

        total_modifications = 0
        events = envelope.get("events", []) or []

        for event in events:
            trace = event.get("trace")
            if not trace or not isinstance(trace, dict):
                continue
            trace_id = trace.get("trace_id", "<unknown>")

            # ── Stage 1: PII scrub ──────────────────────────────────────
            try:
                if scrubber_v2.should_use_v2(level):
                    scrubbed = scrubber_v2.scrub_for_persistence(trace, level)
                    event["trace"] = scrubbed.trace
                    stats = scrubbed.stats or {}
                    # cirislens_core stats expose a few counters; sum
                    # the ones that represent actual substitutions.
                    total_modifications += int(
                        (stats.get("ner_substitutions") or 0)
                        + (stats.get("regex_substitutions") or 0)
                    )
                else:
                    # v1 Python fallback — recursive dict traversal that
                    # mutates strings in place. No counter; conservative
                    # increment of 1 if the call succeeds.
                    scrubbed_v1 = pii_scrubber.scrub_dict_recursive(trace)
                    event["trace"] = scrubbed_v1
                    total_modifications += 1
            except scrubber_v2.ScrubError as e:
                # FSD §6 invariant: no partial-scrub persistence. Reject
                # the batch. Error message is bounded to error class +
                # trace_id; PII content from the source is never echoed.
                logger.warning(
                    "PII scrubber rejected trace %s (level=%s): %s",
                    trace_id, level, type(e).__name__,
                )
                raise ValueError(
                    f"scrubber failed for trace {trace_id} ({type(e).__name__})"
                ) from e
            except ValueError:
                # Invalid level — bubble up; persist surfaces as 422.
                raise
            except Exception as e:
                logger.exception("Unexpected scrubber error on trace %s", trace_id)
                raise ValueError(
                    f"scrubber error on trace {trace_id} ({type(e).__name__})"
                ) from e

            # ── Stage 2: security sanitize (XSS / SQLi / cmd injection) ─
            try:
                trace_for_sanitize = {"components": event["trace"].get("components", [])}
                sanitized, sanit_result = security_sanitizer.sanitize_trace_for_storage(
                    trace_for_sanitize, trace_level=level,
                )
                if sanit_result.fields_modified > 0:
                    # Apply sanitized component data back to the trace.
                    event["trace"]["components"] = sanitized.get(
                        "components", event["trace"]["components"],
                    )
                    total_modifications += sanit_result.fields_modified
                    logger.info(
                        "SECURITY_SANITIZED trace=%s detections=%s modified=%d",
                        trace_id,
                        sanit_result.total_detections,
                        sanit_result.fields_modified,
                    )
            except Exception as e:
                logger.exception("Unexpected sanitizer error on trace %s", trace_id)
                raise ValueError(
                    f"sanitizer error on trace {trace_id} ({type(e).__name__})"
                ) from e

        return envelope, total_modifications

    return scrub_envelope
