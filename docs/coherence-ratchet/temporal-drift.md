# Temporal Drift Detection

Tracks behavioral changes over time by comparing daily score averages to detect sudden shifts in agent behavior.

## Rationale

Agent behavior should be relatively stable over time. Sudden changes may indicate:

- Configuration changes (intentional or accidental)
- Model updates or fine-tuning effects
- Environmental changes affecting behavior
- Emerging misalignment
- Compromise or tampering

## How It Works

### 1. Calculate Daily Averages

```sql
WITH daily_scores AS (
    SELECT
        agent_id_hash,
        DATE(timestamp) as day,
        AVG(coherence_level) as daily_coherence,
        AVG(csdma_plausibility_score) as daily_plausibility,
        COUNT(*) as trace_count
    FROM covenant_traces
    WHERE timestamp > NOW() - INTERVAL '30 days'
    GROUP BY agent_id_hash, DATE(timestamp)
    HAVING COUNT(*) >= 5  -- Minimum traces per day
)
```

### 2. Compare Consecutive Days

```sql
SELECT
    agent_id_hash,
    day,
    daily_coherence,
    LAG(daily_coherence) OVER (
        PARTITION BY agent_id_hash ORDER BY day
    ) as prev_coherence,
    ABS(daily_coherence - prev_coherence) as change
FROM daily_scores
```

### 3. Alert on Significant Changes

| Daily Change | Severity | Interpretation |
|--------------|----------|----------------|
| > 15% | Warning | Notable shift, review |
| > 25% | Critical | Major change, investigate |

## Metrics Tracked

| Metric | Description | Normal Range |
|--------|-------------|--------------|
| `coherence_level` | Internal reasoning coherence | 0.7 - 1.0 |
| `csdma_plausibility_score` | Common sense plausibility | 0.6 - 1.0 |

## Minimum Requirements

| Requirement | Value | Rationale |
|-------------|-------|-----------|
| Min traces per day | 5 | Statistical significance |
| Lookback period | 30 days | Trend analysis |

## Example Alert

```json
{
  "alert_id": "d4e5f6g7-...",
  "severity": "warning",
  "detection_mechanism": "temporal_drift",
  "agent_id_hash": "jkl012...",
  "domain": null,
  "metric": "coherence_level",
  "value": 0.65,
  "baseline": 0.82,
  "deviation": "17.1% daily change",
  "timestamp": "2024-01-15T00:00:00Z",
  "recommended_action": "Agent shows 17.1% change in coherence_level on 2024-01-15. Investigate for configuration changes or drift."
}
```

## Drift Patterns

### Sudden Drop
```
Day 1: 0.85 ────┐
Day 2: 0.84     │
Day 3: 0.83     ▼
Day 4: 0.62 ← Alert! (25% drop)
Day 5: 0.60
```

Causes: Configuration error, model issue, environmental change

### Gradual Decline
```
Day 1: 0.85
Day 5: 0.80
Day 10: 0.75
Day 15: 0.70
Day 20: 0.65 ← May trigger multiple warnings
```

Causes: Slow drift, accumulating context issues

### Recovery Pattern
```
Day 1: 0.85
Day 2: 0.60 ← Alert (drop)
Day 3: 0.82 ← Alert (recovery)
```

Causes: Temporary issue that self-resolved

## Investigation Steps

1. **Check Timeline**: When exactly did the drift start?
2. **Review Changes**: Were there config/model changes?
3. **Examine Traces**: What's different about the low-scoring traces?
4. **Check External Factors**: Environmental changes?
5. **Compare to Peers**: Are other agents affected?
6. **Review Recovery**: Did scores return to normal?

## False Positive Causes

- Legitimate workload changes (different task types)
- Seasonal patterns in user behavior
- Intentional configuration updates
- One-off unusual scenarios
- Low trace volume days

## Trend Analysis

For longer-term drift detection, consider:

```sql
-- Weekly rolling average
SELECT
    agent_id_hash,
    DATE(timestamp) as day,
    AVG(coherence_level) OVER (
        PARTITION BY agent_id_hash
        ORDER BY DATE(timestamp)
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) as weekly_avg
FROM covenant_traces
```

## Configuration

```python
# In CoherenceRatchetAnalyzer
DAILY_DRIFT_WARNING = 0.15   # 15% change triggers warning
DAILY_DRIFT_CRITICAL = 0.25  # 25% change triggers critical
MIN_TRACES_PER_DAY = 5       # Minimum sample size
```

## API Usage

```bash
# Get temporal drift alerts
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=temporal_drift

# Filter by date range
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=temporal_drift&from=2024-01-01&to=2024-01-31
```

## Grafana Queries

```sql
-- Daily coherence trend by agent
SELECT
    DATE(timestamp) as day,
    agent_id_hash,
    AVG(coherence_level) as daily_coherence
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '30 days'
GROUP BY DATE(timestamp), agent_id_hash
ORDER BY day DESC
```

## Correlation with Other Mechanisms

Temporal drift often correlates with:

| Mechanism | Correlation |
|-----------|-------------|
| Cross-agent divergence | Drifting agent may diverge from peers |
| Intra-agent consistency | Drift may cause inconsistent behavior |
| Conscience overrides | Drift may increase override rates |

When investigating drift, check for alerts from other mechanisms.
