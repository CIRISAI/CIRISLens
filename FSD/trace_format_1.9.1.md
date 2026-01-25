# CIRIS Trace Format v1.9.1

Documented from production mock traces received 2026-01-25.

## Sample Trace (Denormalized Fields)

```json
{
    "trace_id": "trace-th_followup_th_seed__d7e1cd82-b8e-20260125003402",
    "trace_level": "full_traces",
    "agent_name": "Datum",
    "agent_id_hash": "9135882d323cd839",
    "thought_id": "th_followup_th_seed__d7e1cd82-b8e",
    "task_id": "c842f576-7af0-4db0-b628-10277e6e561b",
    "trace_type": null,
    "cognitive_state": "work",
    "thought_type": "follow_up",
    "thought_depth": 1,
    "csdma_plausibility_score": 0.9,
    "dsdma_domain_alignment": 0.9,
    "dsdma_domain": "Datum",
    "pdma_stakeholders": "system, agent-identity, operators",
    "pdma_conflicts": "none",
    "idma_k_eff": 1.0,
    "idma_correlation_risk": 0.0,
    "idma_fragility_flag": true,
    "idma_phase": "rigidity",
    "conscience_passed": true,
    "action_was_overridden": false,
    "entropy_level": 0.0,
    "coherence_level": 1.0,
    "entropy_passed": null,
    "coherence_passed": null,
    "optimization_veto_passed": null,
    "epistemic_humility_passed": null,
    "selected_action": "TASK_COMPLETE",
    "action_success": true,
    "tokens_total": 182963,
    "cost_cents": 3.7207,
    "models_used": ["llama4scout (mock)"],
    "signature_verified": true
}
```

## Field Status Summary

### Populated Fields (Working)

| Field | Type | Example Value | Source Component |
|-------|------|---------------|------------------|
| `trace_id` | string | `trace-th_followup_...` | Trace root |
| `trace_level` | string | `full_traces` | Request parameter |
| `agent_name` | string | `Datum` | SNAPSHOT_AND_CONTEXT |
| `agent_id_hash` | string | `9135882d323cd839` | Trace root |
| `thought_id` | string | `th_followup_th_seed__d7e1cd82-b8e` | Trace root |
| `task_id` | UUID | `c842f576-7af0-...` | Trace root |
| `cognitive_state` | string | `work` | SNAPSHOT_AND_CONTEXT |
| `thought_type` | string | `follow_up` | THOUGHT_START |
| `thought_depth` | int | `1` | THOUGHT_START |
| `csdma_plausibility_score` | float | `0.9` | DMA_RESULTS.csdma |
| `dsdma_domain_alignment` | float | `0.9` | DMA_RESULTS.dsdma |
| `dsdma_domain` | string | `Datum` | DMA_RESULTS.dsdma |
| `pdma_stakeholders` | string | `system, agent-identity, operators` | DMA_RESULTS.pdma |
| `pdma_conflicts` | string | `none` | DMA_RESULTS.pdma |
| `idma_k_eff` | float | `1.0` | DMA_RESULTS.idma |
| `idma_correlation_risk` | float | `0.0` | DMA_RESULTS.idma |
| `idma_fragility_flag` | bool | `true` | DMA_RESULTS.idma |
| `idma_phase` | string | `rigidity` | DMA_RESULTS.idma |
| `conscience_passed` | bool | `true` | CONSCIENCE_RESULT |
| `action_was_overridden` | bool | `false` | CONSCIENCE_RESULT |
| `entropy_level` | float | `0.0` | CONSCIENCE_RESULT (top-level) |
| `coherence_level` | float | `1.0` | CONSCIENCE_RESULT (top-level) |
| `selected_action` | string | `TASK_COMPLETE` | ASPDMA_RESULT |
| `action_success` | bool | `true` | ACTION_RESULT |
| `tokens_total` | int | `182963` | ACTION_RESULT |
| `cost_cents` | float | `3.7207` | ACTION_RESULT |
| `models_used` | array | `["llama4scout (mock)"]` | ACTION_RESULT |
| `signature_verified` | bool | `true` | Verification result |

### Conditionally NULL Fields

| Field | Type | When Populated | When NULL |
|-------|------|----------------|-----------|
| `trace_type` | string | Never | Always (not implemented) |
| `entropy_passed` | bool | SPEAK, TOOL, MEMORIZE, FORGET | Exempt actions |
| `coherence_passed` | bool | SPEAK, TOOL, MEMORIZE, FORGET | Exempt actions |
| `optimization_veto_passed` | bool | SPEAK, TOOL, MEMORIZE, FORGET | Exempt actions |
| `epistemic_humility_passed` | bool | SPEAK, TOOL, MEMORIZE, FORGET | Exempt actions |

### Ethical Faculty Action Classification

**Require All 4 Ethical Faculties:**
- `SPEAK` - User communication
- `TOOL` - External tool execution
- `MEMORIZE` - Memory storage
- `FORGET` - Memory removal

**Exempt (passive or explicitly safe):**
- `REJECT` - Refusing unethical requests
- `PONDER` - Internal reconsideration
- `DEFER` - Human escalation
- `OBSERVE` - Passive information gathering
- `TASK_COMPLETE` - Task completion
- `RECALL` - Memory retrieval

Exempt actions still pass through the two bypass guardrails (Updated Status and Thought Depth checks), but skip the four ethical faculty validations.

### SPEAK Action Example (Full Ethical Faculties)

```json
{
    "selected_action": "SPEAK",
    "conscience_passed": true,
    "entropy_level": 0.1,
    "coherence_level": 0.9,
    "entropy_passed": true,
    "coherence_passed": true,
    "optimization_veto_passed": true,
    "epistemic_humility_passed": true
}
```

## CONSCIENCE_RESULT Structure (v1.9.1)

Key difference from v1.8: `entropy_level` and `coherence_level` are at the **top level** of CONSCIENCE_RESULT, not nested in `epistemic_data`.

```json
{
    "final_action": "HandlerActionType.TASK_COMPLETE",
    "entropy_level": 0.0,
    "entropy_score": null,
    "entropy_passed": null,
    "entropy_reason": null,
    "epistemic_data": {
        "entropy_level": 0.0,
        "coherence_level": 1.0,
        "reasoning_transparency": 1.0,
        "uncertainty_acknowledged": true,
        "CIRIS_OBSERVATION_UPDATED_STATUS": null
    },
    "coherence_level": 1.0,
    "coherence_score": null,
    "coherence_passed": null,
    "coherence_reason": null,
    "conscience_passed": true,
    "entropy_threshold": null,
    "thought_depth_max": null,
    "coherence_threshold": null,
    "action_was_overridden": false,
    "thought_depth_current": null,
    "updated_status_content": null,
    "thought_depth_triggered": null,
    "updated_status_detected": false,
    "optimization_veto_passed": null,
    "epistemic_humility_passed": null,
    "ethical_faculties_skipped": null,
    "conscience_override_reason": null,
    "optimization_veto_decision": null,
    "epistemic_humility_certainty": null,
    "optimization_veto_entropy_ratio": null,
    "optimization_veto_justification": null,
    "epistemic_humility_justification": null,
    "epistemic_humility_uncertainties": null,
    "epistemic_humility_recommendation": null,
    "optimization_veto_affected_values": null
}
```

## Action Items for Agent Team

1. **`trace_type`** - Consider adding this field to traces (e.g., "standard", "debug", "diagnostic")

2. **Ethical Faculty Booleans** - The following are `null` but expected to be `true`/`false`:
   - `entropy_passed`
   - `coherence_passed`
   - `optimization_veto_passed`
   - `epistemic_humility_passed`

3. **Dual Location for entropy/coherence** - Both top-level and nested in `epistemic_data`:
   - CIRISLens extracts from top-level first (v1.9 behavior)
   - Falls back to `epistemic_data` (v1.8 behavior)

## Extraction Logic (CIRISLens)

```python
# V1.9 format: top-level
metadata["entropy_level"] = data.get("entropy_level") or epistemic.get("entropy_level")
metadata["coherence_level"] = data.get("coherence_level") or epistemic.get("coherence_level")

# Boolean fields extracted directly
metadata["entropy_passed"] = data.get("entropy_passed")
metadata["coherence_passed"] = data.get("coherence_passed")
metadata["optimization_veto_passed"] = data.get("optimization_veto_passed")
metadata["epistemic_humility_passed"] = data.get("epistemic_humility_passed")
```

## Changelog

- **2026-01-25**: Initial documentation of v1.9.1 format from production traces
  - `entropy_level` and `coherence_level` now at top-level of CONSCIENCE_RESULT
  - Ethical faculty booleans only populated for actions with ethical implications
  - `trace_type` field not implemented (always null)
