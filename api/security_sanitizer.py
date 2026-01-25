"""
Security Sanitizer for CIRISLens

Detects and neutralizes potentially malicious payloads in trace data:
- XSS (script tags, event handlers, javascript: URLs)
- SQL injection patterns
- Oversized payloads (DoS protection)
- Deeply nested structures

Design principles:
- Meaning-preserving: Replace dangerous content with descriptive placeholders
- Provenance-preserving: Hash original content before sanitization
- Defense-in-depth: Multiple detection layers (patterns + size limits)
- Graceful operation: Never crash, always sanitize what we can

Modeled after pii_scrubber.py for consistency.
"""

import hashlib
import html
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Size limits to prevent DoS attacks
SIZE_LIMITS = {
    "max_field_length": 100_000,       # 100KB per text field
    "max_json_depth": 20,               # Recursive depth limit
    "max_component_size": 1_000_000,    # 1MB per component
    "max_trace_size": 10_000_000,       # 10MB total trace
    "max_array_length": 1000,           # Max items in any array
    "max_string_in_identifier": 256,    # trace_id, agent_name, etc.
}

# Dangerous patterns to detect and neutralize
# Patterns are case-insensitive where appropriate
DANGEROUS_PATTERNS: dict[str, re.Pattern] = {
    # XSS patterns
    "xss_script": re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    "xss_script_tag": re.compile(r"<script[^>]*>", re.IGNORECASE),
    "xss_script_close": re.compile(r"</script>", re.IGNORECASE),
    "xss_event_handler": re.compile(r"\bon\w+\s*=\s*[\"'][^\"']*[\"']", re.IGNORECASE),
    "xss_event_handler_unquoted": re.compile(r"\bon\w+\s*=\s*[^\s>]+", re.IGNORECASE),
    "xss_javascript_url": re.compile(r"javascript\s*:[^\"'>\s]*", re.IGNORECASE),
    "xss_vbscript_url": re.compile(r"vbscript\s*:[^\"'>\s]*", re.IGNORECASE),
    "xss_data_url": re.compile(r"data\s*:[^,]*;base64", re.IGNORECASE),
    "xss_iframe": re.compile(r"<iframe[^>]*>.*?</iframe>", re.IGNORECASE | re.DOTALL),
    "xss_iframe_tag": re.compile(r"<iframe[^>]*>", re.IGNORECASE),
    "xss_object": re.compile(r"<object[^>]*>.*?</object>", re.IGNORECASE | re.DOTALL),
    "xss_embed": re.compile(r"<embed[^>]*>", re.IGNORECASE),
    "xss_svg_onload": re.compile(r"<svg[^>]*\s+onload\s*=", re.IGNORECASE),
    "xss_img_onerror": re.compile(r"<img[^>]*\s+onerror\s*=", re.IGNORECASE),
    "xss_body_onload": re.compile(r"<body[^>]*\s+onload\s*=", re.IGNORECASE),
    "xss_style_expression": re.compile(r"expression\s*\(", re.IGNORECASE),
    "xss_style_import": re.compile(r"@import\s+", re.IGNORECASE),

    # SQL injection patterns (common attack signatures)
    "sql_union_select": re.compile(r"\bUNION\s+(?:ALL\s+)?SELECT\b", re.IGNORECASE),
    "sql_drop": re.compile(r"\bDROP\s+(?:TABLE|DATABASE|INDEX)\b", re.IGNORECASE),
    "sql_delete_from": re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    "sql_insert_into": re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE),
    "sql_update_set": re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE),
    "sql_exec": re.compile(r"\b(?:EXEC|EXECUTE)\s*\(", re.IGNORECASE),
    "sql_xp_cmdshell": re.compile(r"\bxp_cmdshell\b", re.IGNORECASE),
    "sql_comment": re.compile(r"(--|#|/\*|\*/)", re.IGNORECASE),
    "sql_or_1_equals_1": re.compile(r"'\s*OR\s+'?\d+'\s*=\s*'?\d+", re.IGNORECASE),
    "sql_semicolon_command": re.compile(r";\s*(?:DROP|DELETE|INSERT|UPDATE|EXEC)", re.IGNORECASE),

    # Command injection patterns
    "cmd_shell": re.compile(r"[;&|`$]\s*(?:sh|bash|cmd|powershell)", re.IGNORECASE),
    "cmd_backtick": re.compile(r"`[^`]+`"),
    "cmd_subshell": re.compile(r"\$\([^)]+\)"),

    # Path traversal
    "path_traversal": re.compile(r"\.\.[\\/]"),

    # Null byte injection
    "null_byte": re.compile(r"%00|\x00"),
}

# Fields to sanitize (same as PII scrubber + identifier fields)
SANITIZE_FIELDS = {
    # Text content fields (from PII scrubber)
    "task_description",
    "initial_context",
    "system_snapshot",
    "gathered_context",
    "relevant_memories",
    "conversation_history",
    "reasoning",
    "prompt_used",
    "combined_analysis",
    "action_rationale",
    "reasoning_summary",
    "action_parameters",
    "aspdma_prompt",
    "conscience_override_reason",
    "epistemic_data",
    "updated_status_content",
    "entropy_reason",
    "coherence_reason",
    "optimization_veto_justification",
    "epistemic_humility_justification",
    "execution_error",
    # Agent identity fields
    "agent_name",
    "domain",
    "codename",
    "agent_id",
    # Identifiers that could carry payloads
    "trace_id",
    "thought_id",
    "task_id",
}

# Identifier fields have stricter length limits
IDENTIFIER_FIELDS = {
    "trace_id",
    "thought_id",
    "task_id",
    "agent_id",
    "agent_id_hash",
    "agent_name",
    "signature_key_id",
}


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SanitizationResult:
    """Result of sanitizing a single text field."""

    original_text: str
    sanitized_text: str
    detections: list[str] = field(default_factory=list)
    was_modified: bool = False
    was_truncated: bool = False


@dataclass
class TraceSanitizationResult:
    """Result of sanitizing an entire trace."""

    original_hash: str                  # SHA-256 of original trace
    sanitized_trace: Any                # The sanitized trace data
    total_detections: list[str] = field(default_factory=list)
    fields_modified: int = 0
    fields_truncated: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    sanitizer_version: str = "1.0.0"


# =============================================================================
# Core Sanitization Functions
# =============================================================================

def compute_content_hash(content: Any) -> str:
    """Compute SHA-256 hash of content for provenance tracking."""
    if isinstance(content, dict):
        # Canonical JSON for consistent hashing
        text = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    elif isinstance(content, str):
        text = content
    else:
        text = str(content)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_patterns(text: str) -> list[str]:
    """Detect dangerous patterns in text, return list of pattern names found."""
    if not text or not isinstance(text, str):
        return []

    detections = []
    for pattern_name, pattern in DANGEROUS_PATTERNS.items():
        if pattern.search(text):
            detections.append(pattern_name)
    return detections


def neutralize_pattern(text: str, pattern_name: str, pattern: re.Pattern) -> str:
    """Replace pattern matches with descriptive placeholder."""
    # Create placeholder based on pattern category
    if pattern_name.startswith("xss_"):
        placeholder = f"[XSS_REMOVED:{pattern_name}]"
    elif pattern_name.startswith("sql_"):
        placeholder = f"[SQL_REMOVED:{pattern_name}]"
    elif pattern_name.startswith("cmd_"):
        placeholder = f"[CMD_REMOVED:{pattern_name}]"
    elif pattern_name.startswith("path_"):
        placeholder = "[PATH_REMOVED]"
    elif pattern_name.startswith("null_"):
        placeholder = "[NULL_BYTE_REMOVED]"
    else:
        placeholder = f"[REMOVED:{pattern_name}]"

    return pattern.sub(placeholder, text)


def sanitize_text(
    text: str,
    max_length: int | None = None,
    is_identifier: bool = False,
) -> SanitizationResult:
    """
    Sanitize a single text field.

    Args:
        text: The text to sanitize
        max_length: Maximum allowed length (truncate if exceeded)
        is_identifier: If True, apply stricter limits for ID fields

    Returns:
        SanitizationResult with sanitized text and detection info
    """
    if not text or not isinstance(text, str):
        return SanitizationResult(
            original_text=str(text) if text else "",
            sanitized_text=str(text) if text else "",
            detections=[],
            was_modified=False,
            was_truncated=False,
        )

    # Determine length limit
    if max_length is None:
        if is_identifier:
            max_length = SIZE_LIMITS["max_string_in_identifier"]
        else:
            max_length = SIZE_LIMITS["max_field_length"]

    result = text
    detections = []
    was_truncated = False

    # Step 1: Truncate if too long
    if len(result) > max_length:
        result = result[:max_length] + "[TRUNCATED]"
        was_truncated = True
        detections.append("size_limit_exceeded")

    # Step 2: Detect and neutralize dangerous patterns
    for pattern_name, pattern in DANGEROUS_PATTERNS.items():
        if pattern.search(result):
            detections.append(pattern_name)
            result = neutralize_pattern(result, pattern_name, pattern)

    # Step 3: HTML entity encode for XSS defense-in-depth
    # Only for content fields, not identifiers (which should be alphanumeric)
    if not is_identifier and result != text:
        # If we already modified it, also escape any remaining HTML
        result = html.escape(result, quote=True)

    was_modified = result != text

    return SanitizationResult(
        original_text=text,
        sanitized_text=result,
        detections=detections,
        was_modified=was_modified,
        was_truncated=was_truncated,
    )


def sanitize_dict_recursive(  # noqa: PLR0912, PLR0915
    data: Any,
    depth: int = 0,
    max_depth: int | None = None,
) -> tuple[Any, list[str], int, int]:
    """
    Recursively sanitize a dictionary or list structure.

    Returns:
        Tuple of (sanitized_data, all_detections, fields_modified, fields_truncated)
    """
    if max_depth is None:
        max_depth = SIZE_LIMITS["max_json_depth"]

    all_detections: list[str] = []
    fields_modified = 0
    fields_truncated = 0

    # Depth limit protection
    if depth > max_depth:
        logger.warning("Sanitization depth limit exceeded at depth %d", depth)
        return "[DEPTH_LIMIT_EXCEEDED]", ["depth_limit_exceeded"], 1, 0

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Sanitize the key itself (could be attack vector)
            safe_key = key
            if isinstance(key, str) and len(key) > SIZE_LIMITS["max_string_in_identifier"]:
                safe_key = key[:SIZE_LIMITS["max_string_in_identifier"]]
                all_detections.append("key_truncated")

            # Check if this field should be sanitized
            if safe_key in SANITIZE_FIELDS and isinstance(value, str):
                is_identifier = safe_key in IDENTIFIER_FIELDS
                san_result = sanitize_text(value, is_identifier=is_identifier)
                result[safe_key] = san_result.sanitized_text
                all_detections.extend(san_result.detections)
                if san_result.was_modified:
                    fields_modified += 1
                if san_result.was_truncated:
                    fields_truncated += 1
            elif isinstance(value, (dict, list)):
                # Recurse
                san_data, det, mod, trunc = sanitize_dict_recursive(
                    value, depth + 1, max_depth
                )
                result[safe_key] = san_data
                all_detections.extend(det)
                fields_modified += mod
                fields_truncated += trunc
            else:
                result[safe_key] = value
        return result, all_detections, fields_modified, fields_truncated

    elif isinstance(data, list):
        # Enforce array length limit
        if len(data) > SIZE_LIMITS["max_array_length"]:
            logger.warning(
                "Array length %d exceeds limit %d, truncating",
                len(data),
                SIZE_LIMITS["max_array_length"],
            )
            data = data[:SIZE_LIMITS["max_array_length"]]
            all_detections.append("array_truncated")

        result = []
        for item in data:
            if isinstance(item, (dict, list)):
                san_data, det, mod, trunc = sanitize_dict_recursive(
                    item, depth + 1, max_depth
                )
                result.append(san_data)
                all_detections.extend(det)
                fields_modified += mod
                fields_truncated += trunc
            elif isinstance(item, str):
                # Sanitize string items in arrays
                san_result = sanitize_text(item)
                result.append(san_result.sanitized_text)
                all_detections.extend(san_result.detections)
                if san_result.was_modified:
                    fields_modified += 1
                if san_result.was_truncated:
                    fields_truncated += 1
            else:
                result.append(item)
        return result, all_detections, fields_modified, fields_truncated

    elif isinstance(data, str):
        san_result = sanitize_text(data)
        return (
            san_result.sanitized_text,
            san_result.detections,
            1 if san_result.was_modified else 0,
            1 if san_result.was_truncated else 0,
        )

    else:
        # Primitives pass through unchanged
        return data, [], 0, 0


def sanitize_trace(trace_data: dict[str, Any]) -> TraceSanitizationResult:
    """
    Sanitize an entire trace, preserving provenance with content hash.

    Args:
        trace_data: The trace dictionary to sanitize

    Returns:
        TraceSanitizationResult with sanitized trace and metadata
    """
    # Step 1: Compute hash of original for provenance
    original_hash = compute_content_hash(trace_data)

    # Step 2: Check total trace size
    try:
        trace_json = json.dumps(trace_data, default=str)
        trace_size = len(trace_json.encode("utf-8"))
        if trace_size > SIZE_LIMITS["max_trace_size"]:
            logger.warning(
                "Trace size %d exceeds limit %d",
                trace_size,
                SIZE_LIMITS["max_trace_size"],
            )
            # We still process it but log the violation
    except (TypeError, ValueError) as e:
        logger.error("Failed to serialize trace for size check: %s", e)

    # Step 3: Recursively sanitize
    sanitized, detections, modified, truncated = sanitize_dict_recursive(trace_data)

    # Step 4: Log if anything was detected
    if detections:
        unique_detections = sorted(set(detections))
        logger.warning(
            "SECURITY_SANITIZATION: hash=%s detections=%s modified=%d truncated=%d",
            original_hash[:16],
            unique_detections,
            modified,
            truncated,
        )

    return TraceSanitizationResult(
        original_hash=original_hash,
        sanitized_trace=sanitized,
        total_detections=list(set(detections)),
        fields_modified=modified,
        fields_truncated=truncated,
    )


# =============================================================================
# Validation Functions (Pre-storage checks)
# =============================================================================

def validate_identifier(
    value: str | None,
    field_name: str,
    max_length: int | None = None,
) -> tuple[str | None, list[str]]:
    """
    Validate and sanitize an identifier field.

    Returns:
        Tuple of (sanitized_value, list_of_issues)
    """
    if value is None:
        return None, []

    if not isinstance(value, str):
        return str(value)[:SIZE_LIMITS["max_string_in_identifier"]], ["type_coerced"]

    issues = []
    result = value

    # Length check
    limit = max_length or SIZE_LIMITS["max_string_in_identifier"]
    if len(result) > limit:
        result = result[:limit]
        issues.append(f"{field_name}_truncated")

    # Pattern check
    detections = detect_patterns(result)
    if detections:
        issues.extend(detections)
        # For identifiers, strip the dangerous content entirely
        for pattern in DANGEROUS_PATTERNS.values():
            result = pattern.sub("", result)

    return result, issues


def validate_numeric(
    value: Any,
    field_name: str,
    min_val: float | None = None,
    max_val: float | None = None,
) -> tuple[float | None, list[str]]:
    """
    Validate a numeric field is within expected bounds.

    Returns:
        Tuple of (validated_value, list_of_issues)
    """
    if value is None:
        return None, []

    issues = []

    try:
        num_value = float(value)
    except (TypeError, ValueError):
        return None, [f"{field_name}_invalid_type"]

    # Check for NaN/Inf
    if math.isnan(num_value):
        return None, [f"{field_name}_is_nan"]
    if abs(num_value) == float("inf"):
        return None, [f"{field_name}_is_infinite"]

    # Bounds checking
    if min_val is not None and num_value < min_val:
        issues.append(f"{field_name}_below_min")
        num_value = min_val
    if max_val is not None and num_value > max_val:
        issues.append(f"{field_name}_above_max")
        num_value = max_val

    return num_value, issues


def validate_score(value: Any, field_name: str) -> tuple[float | None, list[str]]:
    """Validate a 0.0-1.0 score field."""
    return validate_numeric(value, field_name, min_val=0.0, max_val=1.0)


def validate_models_used(models_used: Any) -> tuple[list[str], list[str]]:
    """
    Validate and sanitize the models_used field.

    Returns:
        Tuple of (sanitized_list, issues)
    """
    if models_used is None:
        return [], []

    issues = []

    # Handle string that might be JSON
    if isinstance(models_used, str):
        try:
            models_used = json.loads(models_used)
        except json.JSONDecodeError:
            # Single model name as string
            models_used = [models_used]
        issues.append("models_used_was_string")

    if not isinstance(models_used, list):
        return [], ["models_used_invalid_type"]

    # Sanitize each model name
    result = []
    for model in models_used:
        if model is None:
            continue
        model_str = str(model)
        san_result = sanitize_text(
            model_str,
            max_length=SIZE_LIMITS["max_string_in_identifier"],
            is_identifier=True,
        )
        if san_result.detections:
            issues.extend(san_result.detections)
        result.append(san_result.sanitized_text)

    # Limit array length
    if len(result) > SIZE_LIMITS["max_array_length"]:
        result = result[:SIZE_LIMITS["max_array_length"]]
        issues.append("models_used_truncated")

    return result, issues


# =============================================================================
# High-Level API
# =============================================================================

def sanitize_trace_for_storage(
    trace: Any,
    trace_level: str = "generic",
) -> tuple[Any, TraceSanitizationResult]:
    """
    Prepare a trace for database storage with full sanitization.

    This is the main entry point for trace sanitization.

    Args:
        trace: The trace object (Pydantic model or dict)
        trace_level: The trace level (generic, detailed, full_traces)

    Returns:
        Tuple of (sanitized_trace, sanitization_result)
    """
    # Convert Pydantic model to dict if needed
    if hasattr(trace, "model_dump"):
        trace_dict = trace.model_dump()
    elif hasattr(trace, "dict"):
        trace_dict = trace.dict()
    elif isinstance(trace, dict):
        trace_dict = trace
    else:
        logger.error("Unknown trace type: %s", type(trace))
        trace_dict = {"_raw": str(trace)}

    # Perform sanitization
    result = sanitize_trace(trace_dict)

    logger.debug(
        "Sanitized trace: level=%s hash=%s detections=%d modified=%d",
        trace_level,
        result.original_hash[:16],
        len(result.total_detections),
        result.fields_modified,
    )

    return result.sanitized_trace, result
