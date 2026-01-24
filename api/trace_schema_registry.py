"""
Trace Schema Registry for CIRISLens

Defines known trace schema versions and validates incoming traces against them.
This is the first line of defense - unknown schemas are routed to secure handling.

Security Model:
- VALID schema → Production storage via version-specific adapter
- INVALID but MOCK → Mock repository (dev/testing)
- INVALID and NOT MOCK → Secure malformation logger (potential attack)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SchemaVersion(str, Enum):
    """Known trace schema versions."""

    V1_8 = "1.8"  # Original format with 6 component event_types
    V1_9 = "1.9"  # Updated format (TBD - needs trace sample)
    UNKNOWN = "unknown"


@dataclass
class SchemaValidationResult:
    """Result of schema validation."""

    is_valid: bool
    schema_version: SchemaVersion
    errors: list[str]
    warnings: list[str]
    detected_event_types: list[str]


# =============================================================================
# Schema Definitions
# =============================================================================

# Expected component event_types for each schema version
SCHEMA_DEFINITIONS: dict[SchemaVersion, dict[str, Any]] = {
    SchemaVersion.V1_8: {
        "description": "Original CIRIS trace format (1.8.x agents)",
        "required_event_types": {
            "THOUGHT_START",
            "SNAPSHOT_AND_CONTEXT",
            "DMA_RESULTS",
            "ASPDMA_RESULT",
            "CONSCIENCE_RESULT",
            "ACTION_RESULT",
        },
        "optional_event_types": set(),
        "min_components": 6,
        "max_components": 6,
        # Field paths for data extraction
        "field_paths": {
            "agent_name": ["SNAPSHOT_AND_CONTEXT", "data", "system_snapshot", "agent_identity", "agent_id"],
            "models_used": ["ACTION_RESULT", "data", "models_used"],
            "thought_type": ["THOUGHT_START", "data", "thought_type"],
            "thought_depth": ["THOUGHT_START", "data", "thought_depth"],
            "cognitive_state": ["SNAPSHOT_AND_CONTEXT", "data", "cognitive_state"],
            "csdma_plausibility_score": ["DMA_RESULTS", "data", "csdma", "plausibility_score"],
            "dsdma_domain_alignment": ["DMA_RESULTS", "data", "dsdma", "domain_alignment"],
            "dsdma_domain": ["DMA_RESULTS", "data", "dsdma", "domain"],
            "idma_k_eff": ["DMA_RESULTS", "data", "idma", "k_eff"],
            "idma_fragility_flag": ["DMA_RESULTS", "data", "idma", "fragility_flag"],
            "idma_phase": ["DMA_RESULTS", "data", "idma", "phase"],
            "action_rationale": ["ASPDMA_RESULT", "data", "action_rationale"],
            "selected_action": ["ASPDMA_RESULT", "data", "selected_action"],
            "conscience_passed": ["CONSCIENCE_RESULT", "data", "conscience_passed"],
            "action_was_overridden": ["CONSCIENCE_RESULT", "data", "action_was_overridden"],
            "entropy_level": ["CONSCIENCE_RESULT", "data", "epistemic_data", "entropy_level"],
            "coherence_level": ["CONSCIENCE_RESULT", "data", "epistemic_data", "coherence_level"],
            "tokens_total": ["ACTION_RESULT", "data", "tokens_total"],
            "cost_cents": ["ACTION_RESULT", "data", "cost_cents"],
        },
    },
    # V1_9 will be added once we capture the actual format
    SchemaVersion.V1_9: {
        "description": "Updated CIRIS trace format (1.9.x agents) - PLACEHOLDER",
        "required_event_types": set(),  # TBD
        "optional_event_types": set(),
        "min_components": 0,
        "max_components": 20,
        "field_paths": {},  # TBD
    },
}


# =============================================================================
# Schema Detection and Validation
# =============================================================================


def detect_schema_version(event_types: set[str]) -> SchemaVersion:
    """
    Detect which schema version a trace belongs to based on its event_types.

    Returns SchemaVersion.UNKNOWN if no match found.
    """
    # Check V1_8 first (most common)
    v1_8_required = SCHEMA_DEFINITIONS[SchemaVersion.V1_8]["required_event_types"]
    if event_types == v1_8_required:
        return SchemaVersion.V1_8

    # Check if it's a subset (might be partial trace)
    if event_types.issubset(v1_8_required) and len(event_types) >= 4:
        logger.warning(
            "Trace has partial V1_8 event_types: %s (missing: %s)",
            event_types,
            v1_8_required - event_types,
        )
        return SchemaVersion.V1_8

    # V1_9 detection will be added once we know the format
    # For now, return UNKNOWN for anything else
    return SchemaVersion.UNKNOWN


def validate_trace_schema(
    trace_id: str,
    components: list[dict[str, Any]],
) -> SchemaValidationResult:
    """
    Validate a trace against known schemas.

    Returns validation result with schema version (or UNKNOWN if invalid).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Extract event_types from components
    event_types: set[str] = set()
    for comp in components:
        if isinstance(comp, dict):
            et = comp.get("event_type")
        else:
            # Pydantic model
            et = getattr(comp, "event_type", None)
        if et:
            event_types.add(et)

    detected_list = sorted(event_types)

    # Detect schema version
    schema_version = detect_schema_version(event_types)

    if schema_version == SchemaVersion.UNKNOWN:
        errors.append(
            f"Unknown schema: event_types {detected_list} do not match any known version"
        )
        return SchemaValidationResult(
            is_valid=False,
            schema_version=SchemaVersion.UNKNOWN,
            errors=errors,
            warnings=warnings,
            detected_event_types=detected_list,
        )

    # Validate against detected schema
    schema_def = SCHEMA_DEFINITIONS[schema_version]

    # Check component count
    if len(components) < schema_def["min_components"]:
        errors.append(
            f"Too few components: {len(components)} < {schema_def['min_components']}"
        )
    if len(components) > schema_def["max_components"]:
        warnings.append(
            f"More components than expected: {len(components)} > {schema_def['max_components']}"
        )

    # Check for missing required event_types
    missing = schema_def["required_event_types"] - event_types
    if missing:
        errors.append(f"Missing required event_types: {sorted(missing)}")

    # Check for unexpected event_types
    all_known = schema_def["required_event_types"] | schema_def["optional_event_types"]
    unexpected = event_types - all_known
    if unexpected:
        warnings.append(f"Unexpected event_types (ignored): {sorted(unexpected)}")

    is_valid = len(errors) == 0

    logger.info(
        "Schema validation for %s: version=%s valid=%s errors=%d warnings=%d",
        trace_id,
        schema_version.value,
        is_valid,
        len(errors),
        len(warnings),
    )

    return SchemaValidationResult(
        is_valid=is_valid,
        schema_version=schema_version,
        errors=errors,
        warnings=warnings,
        detected_event_types=detected_list,
    )


def get_schema_field_paths(schema_version: SchemaVersion) -> dict[str, list[str]]:
    """Get field extraction paths for a schema version."""
    if schema_version not in SCHEMA_DEFINITIONS:
        return {}
    return SCHEMA_DEFINITIONS[schema_version].get("field_paths", {})


def register_schema_version(
    version: SchemaVersion,
    required_event_types: set[str],
    optional_event_types: set[str] | None = None,
    min_components: int = 1,
    max_components: int = 20,
    field_paths: dict[str, list[str]] | None = None,
    description: str = "",
) -> None:
    """
    Register a new schema version dynamically.

    This allows adding support for new trace formats without code changes.
    """
    SCHEMA_DEFINITIONS[version] = {
        "description": description,
        "required_event_types": required_event_types,
        "optional_event_types": optional_event_types or set(),
        "min_components": min_components,
        "max_components": max_components,
        "field_paths": field_paths or {},
    }
    logger.info("Registered schema version %s: %s", version.value, description)
