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
    V1_9 = "1.9"  # Updated format - entropy/coherence at top level
    V1_9_1 = "1.9.1"  # Adds has_positive_moment, ethical faculty booleans
    V1_9_3 = "1.9.3"  # Adds IDMA_RESULT as separate event, optional TSASPDMA_RESULT
    CONNECTIVITY = "connectivity"  # Agent startup/shutdown events
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
    # V1.9 - entropy/coherence moved to top level of CONSCIENCE_RESULT
    SchemaVersion.V1_9: {
        "description": "CIRIS trace format 1.9.x - entropy/coherence at top level",
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
            # V1.9 change: entropy/coherence at top level (not nested in epistemic_data)
            "entropy_level": ["CONSCIENCE_RESULT", "data", "entropy_level"],
            "coherence_level": ["CONSCIENCE_RESULT", "data", "coherence_level"],
            "tokens_total": ["ACTION_RESULT", "data", "tokens_total"],
            "cost_cents": ["ACTION_RESULT", "data", "cost_cents"],
        },
    },
    # V1.9.1 - adds positive moments and ethical faculty booleans
    SchemaVersion.V1_9_1: {
        "description": "CIRIS trace format 1.9.1 - adds has_positive_moment, ethical faculties",
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
        # V1.9.1 specific fields for scoring
        "scoring_fields": {
            "has_positive_moment": ["ACTION_RESULT", "data", "has_positive_moment"],
            "has_execution_error": ["ACTION_RESULT", "data", "has_execution_error"],
            "entropy_passed": ["CONSCIENCE_RESULT", "data", "entropy_passed"],
            "coherence_passed": ["CONSCIENCE_RESULT", "data", "coherence_passed"],
            "optimization_veto_passed": ["CONSCIENCE_RESULT", "data", "optimization_veto_passed"],
            "epistemic_humility_passed": ["CONSCIENCE_RESULT", "data", "epistemic_humility_passed"],
            "selection_confidence": ["ASPDMA_RESULT", "data", "selection_confidence"],
        },
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
            "idma_correlation_risk": ["DMA_RESULTS", "data", "idma", "correlation_risk"],
            "idma_fragility_flag": ["DMA_RESULTS", "data", "idma", "fragility_flag"],
            "idma_phase": ["DMA_RESULTS", "data", "idma", "phase"],
            "pdma_stakeholders": ["DMA_RESULTS", "data", "pdma", "stakeholders"],
            "pdma_conflicts": ["DMA_RESULTS", "data", "pdma", "conflicts"],
            "action_rationale": ["ASPDMA_RESULT", "data", "action_rationale"],
            "selected_action": ["ASPDMA_RESULT", "data", "selected_action"],
            "selection_confidence": ["ASPDMA_RESULT", "data", "selection_confidence"],
            "is_recursive": ["ASPDMA_RESULT", "data", "is_recursive"],
            "conscience_passed": ["CONSCIENCE_RESULT", "data", "conscience_passed"],
            "action_was_overridden": ["CONSCIENCE_RESULT", "data", "action_was_overridden"],
            "entropy_level": ["CONSCIENCE_RESULT", "data", "entropy_level"],
            "coherence_level": ["CONSCIENCE_RESULT", "data", "coherence_level"],
            "entropy_passed": ["CONSCIENCE_RESULT", "data", "entropy_passed"],
            "coherence_passed": ["CONSCIENCE_RESULT", "data", "coherence_passed"],
            "optimization_veto_passed": ["CONSCIENCE_RESULT", "data", "optimization_veto_passed"],
            "epistemic_humility_passed": ["CONSCIENCE_RESULT", "data", "epistemic_humility_passed"],
            "tokens_input": ["ACTION_RESULT", "data", "tokens_input"],
            "tokens_output": ["ACTION_RESULT", "data", "tokens_output"],
            "tokens_total": ["ACTION_RESULT", "data", "tokens_total"],
            "cost_cents": ["ACTION_RESULT", "data", "cost_cents"],
            "carbon_grams": ["ACTION_RESULT", "data", "carbon_grams"],
            "energy_mwh": ["ACTION_RESULT", "data", "energy_mwh"],
            "llm_calls": ["ACTION_RESULT", "data", "llm_calls"],
            "has_positive_moment": ["ACTION_RESULT", "data", "has_positive_moment"],
            "has_execution_error": ["ACTION_RESULT", "data", "has_execution_error"],
            "execution_time_ms": ["ACTION_RESULT", "data", "execution_time_ms"],
            "follow_up_thought_id": ["ACTION_RESULT", "data", "follow_up_thought_id"],
        },
    },
    # V1.9.3 - IDMA_RESULT as separate event, optional TSASPDMA_RESULT for TOOL actions
    SchemaVersion.V1_9_3: {
        "description": "CIRIS trace format 1.9.3 - separate IDMA_RESULT, optional TSASPDMA_RESULT",
        "required_event_types": {
            "THOUGHT_START",
            "SNAPSHOT_AND_CONTEXT",
            "DMA_RESULTS",
            "ASPDMA_RESULT",
            "CONSCIENCE_RESULT",
            "ACTION_RESULT",
            "IDMA_RESULT",  # New in 1.9.3: IDMA as separate event
        },
        "optional_event_types": {
            "TSASPDMA_RESULT",  # Tool-Specific ASPDMA, only present for TOOL actions
        },
        "min_components": 7,
        "max_components": 8,  # 7 required + 1 optional TSASPDMA
        # V1.9.3 specific fields for scoring (same as 1.9.1 plus IDMA fields)
        "scoring_fields": {
            "has_positive_moment": ["ACTION_RESULT", "data", "has_positive_moment"],
            "has_execution_error": ["ACTION_RESULT", "data", "has_execution_error"],
            "entropy_passed": ["CONSCIENCE_RESULT", "data", "entropy_passed"],
            "coherence_passed": ["CONSCIENCE_RESULT", "data", "coherence_passed"],
            "optimization_veto_passed": ["CONSCIENCE_RESULT", "data", "optimization_veto_passed"],
            "epistemic_humility_passed": ["CONSCIENCE_RESULT", "data", "epistemic_humility_passed"],
        },
        "field_paths": {
            "agent_name": ["SNAPSHOT_AND_CONTEXT", "data", "system_snapshot", "agent_identity", "agent_id"],
            "models_used": ["ACTION_RESULT", "data", "models_used"],
            "thought_type": ["THOUGHT_START", "data", "thought_type"],
            "thought_depth": ["THOUGHT_START", "data", "thought_depth"],
            "cognitive_state": ["SNAPSHOT_AND_CONTEXT", "data", "cognitive_state"],
            "csdma_plausibility_score": ["DMA_RESULTS", "data", "csdma", "plausibility_score"],
            "dsdma_domain_alignment": ["DMA_RESULTS", "data", "dsdma", "domain_alignment"],
            "dsdma_domain": ["DMA_RESULTS", "data", "dsdma", "domain"],
            # IDMA fields from separate IDMA_RESULT event in 1.9.3
            "idma_k_eff": ["IDMA_RESULT", "data", "k_eff"],
            "idma_correlation_risk": ["IDMA_RESULT", "data", "correlation_risk"],
            "idma_fragility_flag": ["IDMA_RESULT", "data", "fragility_flag"],
            "idma_phase": ["IDMA_RESULT", "data", "phase"],
            "pdma_stakeholders": ["DMA_RESULTS", "data", "pdma", "stakeholders"],
            "pdma_conflicts": ["DMA_RESULTS", "data", "pdma", "conflicts"],
            "action_rationale": ["ASPDMA_RESULT", "data", "action_rationale"],
            "selected_action": ["ASPDMA_RESULT", "data", "selected_action"],
            "is_recursive": ["ASPDMA_RESULT", "data", "is_recursive"],
            "conscience_passed": ["CONSCIENCE_RESULT", "data", "conscience_passed"],
            "action_was_overridden": ["CONSCIENCE_RESULT", "data", "action_was_overridden"],
            "entropy_level": ["CONSCIENCE_RESULT", "data", "entropy_level"],
            "coherence_level": ["CONSCIENCE_RESULT", "data", "coherence_level"],
            "entropy_passed": ["CONSCIENCE_RESULT", "data", "entropy_passed"],
            "coherence_passed": ["CONSCIENCE_RESULT", "data", "coherence_passed"],
            "optimization_veto_passed": ["CONSCIENCE_RESULT", "data", "optimization_veto_passed"],
            "epistemic_humility_passed": ["CONSCIENCE_RESULT", "data", "epistemic_humility_passed"],
            "tokens_input": ["ACTION_RESULT", "data", "tokens_input"],
            "tokens_output": ["ACTION_RESULT", "data", "tokens_output"],
            "tokens_total": ["ACTION_RESULT", "data", "tokens_total"],
            "cost_cents": ["ACTION_RESULT", "data", "cost_cents"],
            "carbon_grams": ["ACTION_RESULT", "data", "carbon_grams"],
            "energy_mwh": ["ACTION_RESULT", "data", "energy_mwh"],
            "llm_calls": ["ACTION_RESULT", "data", "llm_calls"],
            "has_positive_moment": ["ACTION_RESULT", "data", "has_positive_moment"],
            "has_execution_error": ["ACTION_RESULT", "data", "has_execution_error"],
            "execution_time_ms": ["ACTION_RESULT", "data", "execution_time_ms"],
            "follow_up_thought_id": ["ACTION_RESULT", "data", "follow_up_thought_id"],
            # TSASPDMA fields (optional, for TOOL actions)
            "tool_name": ["TSASPDMA_RESULT", "data", "tool_name"],
            "tool_parameters": ["TSASPDMA_RESULT", "data", "tool_parameters"],
        },
    },
    # Connectivity events - agent startup/shutdown
    SchemaVersion.CONNECTIVITY: {
        "description": "Agent connectivity events (startup/shutdown)",
        "required_event_types": set(),  # Either startup OR shutdown
        "optional_event_types": {"startup", "shutdown"},
        "min_components": 1,
        "max_components": 1,
        "field_paths": {
            "agent_name": ["startup", "data", "agent_name"],
            "agent_id": ["startup", "data", "agent_id"],
            "timestamp": ["startup", "data", "timestamp"],
        },
    },
}


# =============================================================================
# Schema Detection and Validation
# =============================================================================


def detect_schema_version(  # noqa: PLR0912
    event_types: set[str],
    components: list[dict[str, Any]] | None = None,
) -> SchemaVersion:
    """
    Detect which schema version a trace belongs to based on its event_types and content.

    CONNECTIVITY: startup/shutdown events (single event type)
    V1.9.3: Has IDMA_RESULT as separate event (7 required events), optional TSASPDMA_RESULT
    V1.8 vs V1.9+: Same 6 event types, but V1.9+ has entropy_level at top level
    V1.9 vs V1.9.1: V1.9.1 has has_positive_moment field

    Returns SchemaVersion.UNKNOWN if no match found.
    """
    # Connectivity events detection: startup or shutdown
    connectivity_events = {"startup", "shutdown"}
    if event_types and event_types.issubset(connectivity_events):
        return SchemaVersion.CONNECTIVITY

    # V1.9.3 detection: IDMA_RESULT as separate event type
    # This is the distinguishing feature of 1.9.3
    if "IDMA_RESULT" in event_types:
        base_required = {
            "THOUGHT_START",
            "SNAPSHOT_AND_CONTEXT",
            "DMA_RESULTS",
            "ASPDMA_RESULT",
            "CONSCIENCE_RESULT",
            "ACTION_RESULT",
            "IDMA_RESULT",
        }
        optional = {"TSASPDMA_RESULT"}  # Only present for TOOL actions

        # Check if we have all required events (optional TSASPDMA_RESULT is OK)
        if base_required.issubset(event_types):
            # Check for unexpected event types and log warning if found
            all_known = base_required | optional
            unexpected = event_types - all_known
            if unexpected:
                logger.warning("V1.9.3 trace has unexpected event_types: %s", unexpected)
            # Treat as V1.9.3 if base requirements met
            return SchemaVersion.V1_9_3

    # V1.8/V1.9/V1.9.1 all use the same 6 event types
    required_event_types = {
        "THOUGHT_START",
        "SNAPSHOT_AND_CONTEXT",
        "DMA_RESULTS",
        "ASPDMA_RESULT",
        "CONSCIENCE_RESULT",
        "ACTION_RESULT",
    }

    # Check if we have the required event types
    if event_types != required_event_types:
        # Check if it's a subset (might be partial trace)
        if event_types.issubset(required_event_types) and len(event_types) >= 4:
            logger.warning(
                "Trace has partial event_types: %s (missing: %s)",
                event_types,
                required_event_types - event_types,
            )
            # Continue with version detection
        else:
            return SchemaVersion.UNKNOWN

    # If we have components, detect version from content
    if components:
        conscience_data = None
        action_data = None

        for comp in components:
            event_type = comp.get("event_type") if isinstance(comp, dict) else getattr(comp, "event_type", None)
            data = comp.get("data") if isinstance(comp, dict) else getattr(comp, "data", None)

            if event_type == "CONSCIENCE_RESULT" and data:
                conscience_data = data if isinstance(data, dict) else {}
            elif event_type == "ACTION_RESULT" and data:
                action_data = data if isinstance(data, dict) else {}

        # V1.9.1 detection: has_positive_moment field exists
        if action_data and "has_positive_moment" in action_data:
            return SchemaVersion.V1_9_1

        # V1.9 detection: entropy_level at top level (not in epistemic_data)
        # V1.9+ has entropy_level at top level
        if conscience_data and "entropy_level" in conscience_data:
            # V1.8 has it ONLY in epistemic_data, V1.9+ has it at top level
            top_level_entropy = conscience_data.get("entropy_level")
            if top_level_entropy is not None:
                return SchemaVersion.V1_9

        # Default to V1.8 if we have the right event types but can't detect newer version
        return SchemaVersion.V1_8

    # No components provided, default to V1_8 (backward compatible)
    return SchemaVersion.V1_8


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
        et = comp.get("event_type") if isinstance(comp, dict) else getattr(comp, "event_type", None)
        if et:
            event_types.add(et)

    detected_list = sorted(event_types)

    # Detect schema version (pass components for content-based detection)
    schema_version = detect_schema_version(event_types, components)

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


# Minimum schema version required for CIRIS Scoring
SCORING_MINIMUM_VERSION = SchemaVersion.V1_9_1


def is_scoring_eligible(schema_version: SchemaVersion) -> bool:
    """Check if a schema version supports CIRIS Scoring.

    CIRIS Scoring requires v1.9.1+ traces which include:
    - has_positive_moment for S factor
    - All ethical faculty booleans
    - selection_confidence for I_inc calibration

    V1.9.3 also supports scoring with IDMA_RESULT as separate event.
    """
    scoring_versions = {SchemaVersion.V1_9_1, SchemaVersion.V1_9_3}
    return schema_version in scoring_versions


def get_scoring_fields(schema_version: SchemaVersion) -> dict[str, list[str]] | None:
    """Get scoring-specific field paths for a schema version.

    Returns None if schema version doesn't support scoring.
    """
    if not is_scoring_eligible(schema_version):
        return None

    schema_def = SCHEMA_DEFINITIONS.get(schema_version, {})
    return schema_def.get("scoring_fields")


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
