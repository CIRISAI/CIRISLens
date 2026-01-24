"""
Secure Malformation Logger for CIRISLens

Handles traces that fail schema validation and are not mock traces.
These could be:
1. Legitimate traces from an unknown schema version (needs adapter)
2. Malformed traces from buggy agents
3. Potential attack payloads

Security principles:
- NEVER store raw payload content (potential XSS, injection vectors)
- Only store metadata + cryptographic hash of payload
- Log structure for forensic analysis without exposure
- Alert on anomalies (high rate, unusual patterns)
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class MalformationRecord:
    """Record of a malformed trace for audit purposes."""

    record_id: str
    timestamp: datetime
    trace_id: str | None
    source_ip: str | None

    # Schema validation results
    detected_event_types: list[str]
    validation_errors: list[str]
    validation_warnings: list[str]

    # Payload fingerprint (NEVER store actual content)
    payload_sha256: str
    payload_size_bytes: int
    component_count: int

    # Structural metadata (safe to store)
    has_signature: bool
    signature_key_id: str | None
    claimed_thought_id: str | None
    claimed_task_id: str | None

    # Classification
    rejection_reason: str
    severity: str  # "warning", "error", "critical"


def compute_payload_hash(payload: dict[str, Any] | str) -> str:
    """Compute SHA-256 hash of payload without storing content."""
    if isinstance(payload, dict):
        # Canonical JSON for consistent hashing
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        content = str(payload)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def extract_safe_metadata(trace: dict[str, Any] | Any) -> dict[str, Any]:
    """
    Extract only safe metadata from a trace.

    SECURITY: This function must NEVER return text content, only:
    - Identifiers (trace_id, thought_id, etc.)
    - Counts and sizes
    - Boolean flags
    - Cryptographic identifiers
    """
    # Handle both dict and Pydantic model
    if hasattr(trace, "model_dump"):
        trace_dict = trace.model_dump()
    elif hasattr(trace, "__dict__"):
        trace_dict = dict(trace.__dict__)
    elif isinstance(trace, dict):
        trace_dict = trace
    else:
        return {
            "parse_error": "Unable to extract metadata from payload type",
            "payload_type": str(type(trace)),
        }

    safe_meta = {
        "trace_id": trace_dict.get("trace_id"),
        "thought_id": trace_dict.get("thought_id"),
        "task_id": trace_dict.get("task_id"),
        "agent_id_hash": trace_dict.get("agent_id_hash"),
        "has_signature": bool(trace_dict.get("signature")),
        "signature_key_id": trace_dict.get("signature_key_id"),
        "started_at": trace_dict.get("started_at"),
        "completed_at": trace_dict.get("completed_at"),
    }

    # Count components without storing content
    components = trace_dict.get("components", [])
    safe_meta["component_count"] = len(components) if isinstance(components, list) else 0

    # Extract event_types only (no content)
    if isinstance(components, list):
        event_types = []
        for comp in components:
            if isinstance(comp, dict):
                et = comp.get("event_type")
            elif hasattr(comp, "event_type"):
                et = comp.event_type
            else:
                et = None
            if et:
                event_types.append(et)
        safe_meta["event_types"] = event_types

    return safe_meta


async def log_malformed_trace(
    conn,
    trace: Any,
    validation_errors: list[str],
    validation_warnings: list[str],
    detected_event_types: list[str],
    rejection_reason: str,
    source_ip: str | None = None,
) -> MalformationRecord:
    """
    Log a malformed trace securely to the audit table.

    SECURITY: Only stores metadata and hashes, NEVER raw content.
    """
    record_id = str(uuid4())
    timestamp = datetime.now(UTC)

    # Extract safe metadata
    safe_meta = extract_safe_metadata(trace)

    # Compute payload hash
    payload_hash = compute_payload_hash(trace)

    # Estimate payload size
    if hasattr(trace, "model_dump"):
        payload_str = json.dumps(trace.model_dump(), default=str)
    elif isinstance(trace, dict):
        payload_str = json.dumps(trace, default=str)
    else:
        payload_str = str(trace)
    payload_size = len(payload_str.encode("utf-8"))

    # Determine severity
    severity = "error"
    if "attack" in rejection_reason.lower() or "injection" in rejection_reason.lower():
        severity = "critical"
    elif len(validation_errors) == 0:
        severity = "warning"

    record = MalformationRecord(
        record_id=record_id,
        timestamp=timestamp,
        trace_id=safe_meta.get("trace_id"),
        source_ip=source_ip,
        detected_event_types=detected_event_types,
        validation_errors=validation_errors,
        validation_warnings=validation_warnings,
        payload_sha256=payload_hash,
        payload_size_bytes=payload_size,
        component_count=safe_meta.get("component_count", 0),
        has_signature=safe_meta.get("has_signature", False),
        signature_key_id=safe_meta.get("signature_key_id"),
        claimed_thought_id=safe_meta.get("thought_id"),
        claimed_task_id=safe_meta.get("task_id"),
        rejection_reason=rejection_reason,
        severity=severity,
    )

    # Store in database
    await conn.execute(
        """
        INSERT INTO cirislens.malformed_traces (
            record_id, timestamp, trace_id, source_ip,
            detected_event_types, validation_errors, validation_warnings,
            payload_sha256, payload_size_bytes, component_count,
            has_signature, signature_key_id,
            claimed_thought_id, claimed_task_id,
            rejection_reason, severity
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
        )
        """,
        record_id,
        timestamp,
        record.trace_id,
        source_ip,
        detected_event_types,
        validation_errors,
        validation_warnings,
        payload_hash,
        payload_size,
        record.component_count,
        record.has_signature,
        record.signature_key_id,
        record.claimed_thought_id,
        record.claimed_task_id,
        rejection_reason,
        severity,
    )

    # Log for monitoring
    logger.warning(
        "MALFORMED_TRACE: id=%s reason=%s severity=%s hash=%s size=%d errors=%s",
        record.trace_id or "unknown",
        rejection_reason,
        severity,
        payload_hash[:16],
        payload_size,
        validation_errors,
    )

    # Alert on critical severity
    if severity == "critical":
        logger.critical(
            "CRITICAL_MALFORMATION: Potential attack vector detected. "
            "trace_id=%s hash=%s source=%s",
            record.trace_id,
            payload_hash,
            source_ip,
        )

    return record


async def get_malformation_stats(
    conn,
    hours: int = 24,
) -> dict[str, Any]:
    """Get statistics on malformed traces for monitoring."""
    stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total_count,
            COUNT(CASE WHEN severity = 'critical' THEN 1 END) as critical_count,
            COUNT(CASE WHEN severity = 'error' THEN 1 END) as error_count,
            COUNT(CASE WHEN severity = 'warning' THEN 1 END) as warning_count,
            COUNT(DISTINCT source_ip) as unique_sources,
            COUNT(DISTINCT payload_sha256) as unique_payloads
        FROM cirislens.malformed_traces
        WHERE timestamp > NOW() - INTERVAL '%s hours'
        """,
        hours,
    )
    return dict(stats) if stats else {}
