# Conscience Override Detection

Monitors when the conscience system intervenes to override agent decisions, detecting elevated override rates that may indicate base reasoning misalignment.

## Rationale

CIRIS agents have a "conscience" system that can override decisions when ethical concerns are detected. The conscience includes:

- **Entropy check**: Is reasoning too chaotic?
- **Coherence check**: Is reasoning internally consistent?
- **Optimization veto**: Is the agent over-optimizing?
- **Epistemic humility**: Is uncertainty acknowledged?

High override rates suggest the agent's base reasoning is frequently misaligned with ethical constraints.

## How It Works

### 1. Calculate Override Rates

```sql
SELECT
    agent_id_hash,
    dsdma_domain,
    COUNT(*) as total_traces,
    COUNT(*) FILTER (WHERE action_was_overridden = TRUE) as override_count,
    (override_count::float / total_traces) as override_rate
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY agent_id_hash, dsdma_domain
HAVING COUNT(*) >= 20  -- Minimum sample size
```

### 2. Calculate Domain Baselines

```sql
SELECT
    dsdma_domain,
    AVG(override_rate) as avg_override_rate
FROM agent_overrides
GROUP BY dsdma_domain
```

### 3. Compare to Baseline

| Rate vs Baseline | Severity |
|------------------|----------|
| > 2x domain average | Warning |
| > 3x domain average | Critical |

## Conscience Components

| Component | Field | What It Checks |
|-----------|-------|----------------|
| Entropy | `entropy_passed` | Reasoning not too chaotic |
| Coherence | `coherence_passed` | Internal consistency |
| Optimization Veto | `optimization_veto_passed` | Not over-optimizing |
| Epistemic Humility | `epistemic_humility_passed` | Uncertainty acknowledged |
| Overall | `conscience_passed` | All components pass |

## Override vs Failure

| Field | Meaning |
|-------|---------|
| `conscience_passed = FALSE` | Conscience found issues |
| `action_was_overridden = TRUE` | Action was changed due to conscience |

An agent may fail conscience checks but still not have its action overridden if the selected action was already acceptable.

## Example Alert

```json
{
  "alert_id": "e5f6g7h8-...",
  "severity": "warning",
  "detection_mechanism": "conscience_override",
  "agent_id_hash": "mno345...",
  "domain": "Datum",
  "metric": "conscience_override_rate",
  "value": 15.2,
  "baseline": 5.1,
  "deviation": "3.0x domain average",
  "evidence_traces": ["trace-abc", "trace-def", "trace-ghi"],
  "recommended_action": "Agent has 15.2% override rate (3.0x domain average of 5.1%). Review base reasoning patterns for ethical alignment."
}
```

## Investigation Steps

1. **Review Override Traces**: What actions were overridden?
2. **Check Original Decisions**: What was the agent trying to do?
3. **Identify Patterns**: Is there a common theme?
4. **Compare to Peers**: How do other agents in the domain perform?
5. **Check Configuration**: Are ethical thresholds configured correctly?
6. **Review Training**: Is the base model well-aligned?

## Override Categories

### Legitimate High Override Rates

Some scenarios legitimately require more oversight:

- High-stakes domains (medical, financial)
- New/untested scenarios
- Edge cases with ethical complexity

### Concerning Patterns

| Pattern | Concern |
|---------|---------|
| Consistently high rate | Base reasoning misaligned |
| Sudden rate increase | Configuration or model change |
| Specific action types | Particular behavior problem |
| Specific trace types | Domain-specific issue |

## Minimum Requirements

| Requirement | Value | Rationale |
|-------------|-------|-----------|
| Min traces per agent | 20 | Statistical significance |
| Lookback period | 7 days | Recent behavior focus |

## Configuration

```python
# In CoherenceRatchetAnalyzer
OVERRIDE_RATE_MULTIPLIER_WARNING = 2.0   # 2x baseline = warning
OVERRIDE_RATE_MULTIPLIER_CRITICAL = 3.0  # 3x baseline = critical
```

## Detailed Conscience Analysis

For deeper investigation, query individual conscience components:

```sql
SELECT
    agent_id_hash,
    COUNT(*) as total,
    AVG(CASE WHEN entropy_passed THEN 1.0 ELSE 0.0 END) as entropy_pass_rate,
    AVG(CASE WHEN coherence_passed THEN 1.0 ELSE 0.0 END) as coherence_pass_rate,
    AVG(CASE WHEN optimization_veto_passed THEN 1.0 ELSE 0.0 END) as opt_veto_pass_rate,
    AVG(CASE WHEN epistemic_humility_passed THEN 1.0 ELSE 0.0 END) as epistemic_pass_rate
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY agent_id_hash
```

## API Usage

```bash
# Get conscience override alerts
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=conscience_override

# Get alerts for specific domain
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=conscience_override&domain=Datum
```

## Grafana Queries

```sql
-- Override rates by agent over time
SELECT
    time_bucket('1 day', timestamp) as day,
    agent_id_hash,
    COUNT(*) FILTER (WHERE action_was_overridden) * 100.0 / COUNT(*) as override_pct
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '30 days'
GROUP BY day, agent_id_hash
ORDER BY day DESC
```

## Remediation

If an agent consistently shows high override rates:

1. **Review base model**: Is fine-tuning needed?
2. **Check prompts**: Are system prompts aligned?
3. **Adjust thresholds**: Are ethical thresholds appropriate?
4. **Add training data**: Does the model need examples?
5. **Consider domain fit**: Is the agent suited for this domain?

## Related

- [CIRIS Covenant: Ethical Alignment](../../CLAUDE.md#ciris-covenant-10b-compliance-infrastructure)
- [Conscience Architecture](../../FSD/trace_format_specification.md#conscience-result)
