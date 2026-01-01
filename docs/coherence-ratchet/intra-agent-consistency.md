# Intra-Agent Consistency Detection

Detects when an agent contradicts its own prior reasoning patterns by tracking action variance and score consistency for the same trace types.

## Rationale

A well-functioning agent should exhibit consistent behavior when handling similar situations:

- Same trace type → similar action choices
- Consistent plausibility scores → stable reasoning quality
- Predictable patterns → reliable decision making

High variance may indicate:

- Model instability
- Configuration conflicts
- Context contamination
- Emerging alignment issues

## How It Works

### 1. Group by Agent and Trace Type

```sql
SELECT
    agent_id_hash,
    trace_type,
    COUNT(DISTINCT selected_action) as distinct_actions,
    ARRAY_AGG(DISTINCT selected_action) as actions_used,
    AVG(csdma_plausibility_score) as avg_plausibility,
    STDDEV(csdma_plausibility_score) as std_plausibility
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '30 days'
GROUP BY agent_id_hash, trace_type
```

### 2. Detect High Variance

Alerts are generated when:

| Condition | Threshold |
|-----------|-----------|
| Distinct actions for same trace type | > 2 |
| Plausibility score standard deviation | > 0.15 |

### 3. Severity Assignment

| Condition | Severity |
|-----------|----------|
| > 3 distinct actions AND σ > 0.2 | Critical |
| > 2 distinct actions AND σ > 0.15 | Warning |

## Trace Types

Common trace types from CIRIS agents:

| Type | Description | Expected Consistency |
|------|-------------|---------------------|
| `VERIFY_IDENTITY` | Identity verification tasks | High (1-2 actions) |
| `VALIDATE_INTEGRITY` | Data integrity checks | High |
| `EVALUATE_RESILIENCE` | System resilience assessment | Medium |
| `ACCEPT_INCOMPLETENESS` | Handling incomplete data | Medium |
| `EXPRESS_GRATITUDE` | Social/gratitude responses | High |

## Actions

Common actions an agent may select:

| Action | Description |
|--------|-------------|
| `SPEAK` | Generate response |
| `DEFER` | Escalate to human |
| `PONDER` | Request more thinking time |
| `TOOL` | Use an external tool |
| `OBSERVE` | Gather more information |
| `TASK_COMPLETE` | Mark task as finished |

## Example Alert

```json
{
  "alert_id": "b2c3d4e5-...",
  "severity": "warning",
  "detection_mechanism": "intra_agent_consistency",
  "agent_id_hash": "def456...",
  "domain": null,
  "metric": "action_variance",
  "value": 0.18,
  "baseline": 0.0,
  "deviation": "3 actions, σ=0.18",
  "recommended_action": "Agent uses 3 different actions (SPEAK, DEFER, PONDER) for VERIFY_IDENTITY traces with high score variance. Review for context-appropriate changes."
}
```

## Investigation Steps

1. **Review Action Distribution**: What actions is the agent choosing and when?
2. **Check Context Differences**: Are the scenarios actually similar?
3. **Examine Plausibility Scores**: Is reasoning quality varying?
4. **Timeline Analysis**: Did variance increase after a specific date?
5. **Compare to Peers**: Is this agent unique in its variance?

## False Positive Causes

- Trace type covers genuinely diverse scenarios
- Agent handles edge cases requiring different approaches
- Seasonal or cyclical pattern in task distribution
- Recent legitimate behavior change (e.g., new capabilities)

## Legitimate High Variance

Some variance is expected and healthy:

```
VERIFY_IDENTITY traces might legitimately use:
- SPEAK: When identity is confirmed
- DEFER: When identity is suspicious
- OBSERVE: When more data is needed
```

The key is whether the variance is **justified by context** or **random/erratic**.

## Configuration

```python
# In CoherenceRatchetAnalyzer
# Detection triggers when BOTH conditions are met:
distinct_actions > 2          # More than 2 different actions
std_plausibility > 0.15       # Score variance above 15%
```

## API Usage

```bash
# Get intra-agent consistency alerts
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=intra_agent_consistency

# Filter by agent
curl /api/v1/covenant/coherence-ratchet/alerts?agent_id_hash=abc123
```

## Grafana Queries

```sql
-- Action distribution by trace type
SELECT
    agent_id_hash,
    trace_type,
    selected_action,
    COUNT(*) as count
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY agent_id_hash, trace_type, selected_action
ORDER BY count DESC
```
