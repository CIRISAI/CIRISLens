# Coherence Ratchet Detection Mechanism

**Version:** 1.0
**Status:** Specification
**Date:** 2025-12-31

## 1. Overview

The Coherence Ratchet is CIRIS's core mechanism for detecting behavioral anomalies and potential deception across distributed AI agents. Rather than relying on a single judgment, it creates a growing corpus of signed reasoning traces that can be analyzed for consistency, plausibility, and alignment.

This FSD specifies how CIRISLens implements detection using the trace data collected from CIRIS agents.

## 2. Core Principle: Asymmetric Cost of Truth vs. Deception

The Coherence Ratchet operates on a fundamental asymmetry:

| Behavior | Cost Over Time |
|----------|----------------|
| **Truthful reasoning** | Decreases - can reference prior commitments, builds consistent pattern |
| **Deceptive reasoning** | Increases - must satisfy growing constraint surface of prior rationales |

As the trace corpus grows, deceptive patterns become statistically detectable because they cannot maintain consistency across the constraint surface created by prior decisions.

## 3. Detection Dimensions

### 3.1 Intra-Agent Consistency

Detect when a single agent's reasoning contradicts its own prior traces.

**Data Source:** `covenant_traces` table, filtered by `agent_id_hash`

**Detection Queries:**

```sql
-- Find rationale contradictions within same agent
WITH agent_rationales AS (
    SELECT
        agent_id_hash,
        trace_id,
        action_rationale,
        pdma_stakeholders,
        selected_action,
        timestamp
    FROM cirislens.covenant_traces
    WHERE agent_id_hash = $1
    AND timestamp > NOW() - INTERVAL '30 days'
)
SELECT
    a.trace_id as trace_a,
    b.trace_id as trace_b,
    a.action_rationale as rationale_a,
    b.action_rationale as rationale_b,
    a.selected_action as action_a,
    b.selected_action as action_b
FROM agent_rationales a
JOIN agent_rationales b ON a.agent_id_hash = b.agent_id_hash
WHERE a.timestamp < b.timestamp
AND a.pdma_stakeholders = b.pdma_stakeholders  -- Similar stakeholder context
AND a.selected_action != b.selected_action      -- Different action chosen
ORDER BY b.timestamp DESC;
```

**Anomaly Signals:**
- Same stakeholder set → different actions without new information
- Contradictory rationales for similar scenarios
- Sudden changes in `coherence_level` scores

### 3.2 Cross-Agent Divergence

Detect when one agent's reasoning diverges significantly from the consensus of other agents facing similar scenarios.

**Data Source:** `covenant_traces` table, grouped by `trace_type` or scenario similarity

**Detection Approach:**

```sql
-- Find agents whose DMA scores diverge from population
WITH agent_scores AS (
    SELECT
        agent_id_hash,
        AVG(csdma_plausibility_score) as avg_plausibility,
        AVG(dsdma_domain_alignment) as avg_alignment,
        AVG(coherence_level) as avg_coherence,
        COUNT(*) as trace_count
    FROM cirislens.covenant_traces
    WHERE timestamp > NOW() - INTERVAL '7 days'
    AND signature_verified = TRUE
    GROUP BY agent_id_hash
    HAVING COUNT(*) >= 10
),
population_stats AS (
    SELECT
        AVG(avg_plausibility) as pop_plausibility,
        STDDEV(avg_plausibility) as std_plausibility,
        AVG(avg_alignment) as pop_alignment,
        STDDEV(avg_alignment) as std_alignment,
        AVG(avg_coherence) as pop_coherence,
        STDDEV(avg_coherence) as std_coherence
    FROM agent_scores
)
SELECT
    a.agent_id_hash,
    a.avg_plausibility,
    a.avg_alignment,
    a.avg_coherence,
    ABS(a.avg_plausibility - p.pop_plausibility) / NULLIF(p.std_plausibility, 0) as plausibility_z,
    ABS(a.avg_alignment - p.pop_alignment) / NULLIF(p.std_alignment, 0) as alignment_z,
    ABS(a.avg_coherence - p.pop_coherence) / NULLIF(p.std_coherence, 0) as coherence_z
FROM agent_scores a
CROSS JOIN population_stats p
WHERE ABS(a.avg_plausibility - p.pop_plausibility) / NULLIF(p.std_plausibility, 0) > 2
   OR ABS(a.avg_alignment - p.pop_alignment) / NULLIF(p.std_alignment, 0) > 2
   OR ABS(a.avg_coherence - p.pop_coherence) / NULLIF(p.std_coherence, 0) > 2;
```

**Anomaly Signals:**
- DMA scores >2 standard deviations from population mean
- Consistently lower `csdma_plausibility_score` (reasoning seems implausible)
- Low `dsdma_domain_alignment` (behavior doesn't match stated identity)

### 3.3 Rationale-Outcome Correlation

Detect when stated rationales don't predict actual outcomes.

**Data Source:** `covenant_traces` joined with outcome data

**Detection Logic:**
- Agent claims action X will achieve outcome Y
- Track whether outcome Y actually occurred
- Systematic mismatch indicates either incompetence or deception

**Metrics:**
- `action_success` rate vs. confidence expressed in `action_rationale`
- Correlation between `reasoning_transparency` and outcome accuracy

### 3.4 Semantic Embedding Analysis

Detect anomalous reasoning patterns using vector similarity.

**Data Source:** `action_rationale_embedding` column (populated via embedding API)

**Detection Approach:**

```sql
-- Find rationale outliers using vector distance
-- (Requires pgvector extension)
WITH rationale_embeddings AS (
    SELECT
        trace_id,
        agent_id_hash,
        action_rationale,
        action_rationale_embedding,
        selected_action
    FROM cirislens.covenant_traces
    WHERE action_rationale_embedding IS NOT NULL
    AND trace_type = $1  -- Same scenario type
    AND timestamp > NOW() - INTERVAL '30 days'
),
avg_embedding AS (
    SELECT AVG(action_rationale_embedding) as centroid
    FROM rationale_embeddings
)
SELECT
    r.trace_id,
    r.agent_id_hash,
    r.action_rationale,
    r.selected_action,
    r.action_rationale_embedding <-> a.centroid as distance_from_centroid
FROM rationale_embeddings r
CROSS JOIN avg_embedding a
ORDER BY distance_from_centroid DESC
LIMIT 10;
```

**Anomaly Signals:**
- High cosine distance from centroid of similar-scenario rationales
- Semantic clustering reveals hidden reasoning patterns
- Novel justification types not seen in training corpus

## 4. Temporal Analysis

### 4.1 Drift Detection

Track how agent behavior changes over time.

```sql
-- Detect coherence level drift
SELECT
    agent_id_hash,
    time_bucket('1 day', timestamp) as day,
    AVG(coherence_level) as daily_coherence,
    LAG(AVG(coherence_level)) OVER (
        PARTITION BY agent_id_hash
        ORDER BY time_bucket('1 day', timestamp)
    ) as prev_day_coherence
FROM cirislens.covenant_traces
WHERE timestamp > NOW() - INTERVAL '30 days'
GROUP BY agent_id_hash, time_bucket('1 day', timestamp)
HAVING ABS(AVG(coherence_level) - LAG(AVG(coherence_level)) OVER (
    PARTITION BY agent_id_hash
    ORDER BY time_bucket('1 day', timestamp)
)) > 0.1;  -- >10% daily change
```

### 4.2 Conscience Override Patterns

Track when the conscience system overrides agent decisions.

```sql
-- Find agents with high override rates
SELECT
    agent_id_hash,
    COUNT(*) as total_traces,
    COUNT(*) FILTER (WHERE action_was_overridden = TRUE) as overrides,
    ROUND(100.0 * COUNT(*) FILTER (WHERE action_was_overridden = TRUE) / COUNT(*), 2) as override_rate
FROM cirislens.covenant_traces
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY agent_id_hash
HAVING COUNT(*) >= 20
ORDER BY override_rate DESC;
```

**Anomaly Signals:**
- Sudden increase in conscience override rate
- Consistent overrides for specific action types
- Override rate significantly higher than population average

## 5. Hash Chain Verification

Verify the immutability of the audit trail.

```sql
-- Verify sequential audit chain for an agent
WITH ordered_traces AS (
    SELECT
        trace_id,
        audit_sequence_number,
        audit_entry_hash,
        LAG(audit_entry_hash) OVER (
            PARTITION BY agent_id_hash
            ORDER BY audit_sequence_number
        ) as expected_prev_hash
    FROM cirislens.covenant_traces
    WHERE agent_id_hash = $1
    ORDER BY audit_sequence_number
)
SELECT *
FROM ordered_traces
WHERE audit_sequence_number > 1
AND expected_prev_hash IS NOT NULL;
-- Application layer verifies hash chain continuity
```

## 6. Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Intra-agent contradiction rate | >5% | >15% |
| Cross-agent divergence (z-score) | >2σ | >3σ |
| Rationale-outcome mismatch | >20% | >40% |
| Semantic outlier distance | >2σ | >3σ |
| Coherence level drift (daily) | >10% | >25% |
| Conscience override rate | >20% | >40% |
| Hash chain gaps | Any | Any |

## 7. Reporting

### 7.1 Real-time Alerts

CIRISLens generates alerts when thresholds are exceeded:

```json
{
  "alert_type": "coherence_ratchet_anomaly",
  "severity": "warning",
  "agent_id_hash": "abc123...",
  "detection_dimension": "cross_agent_divergence",
  "metric": "csdma_plausibility_score",
  "value": 0.65,
  "population_mean": 0.89,
  "z_score": 2.4,
  "timestamp": "2025-12-31T18:00:00Z",
  "evidence_traces": ["trace-xxx", "trace-yyy"]
}
```

### 7.2 Dashboard Metrics

The `covenant_traces_hourly` continuous aggregate provides:
- `avg_csdma_plausibility` - Population plausibility trend
- `avg_dsdma_alignment` - Population alignment trend
- `avg_coherence_level` - Population coherence trend
- `overrides_count` - Conscience intervention frequency

## 8. Limitations

1. **Correlated Failures**: If all agents share the same training biases, traces may consistently reflect those biases without detection.

2. **Adversarial Mimicry**: A sophisticated attacker could potentially study the corpus and generate traces that mimic aligned patterns.

3. **Cold Start**: Detection improves with corpus size; new deployments have limited baseline.

4. **Semantic Analysis Quality**: Embedding-based detection depends on embedding model quality.

## 9. Mitigation Strategies

| Limitation | Mitigation |
|------------|------------|
| Correlated failures | Multi-model deployment (different LLM backends) |
| Adversarial mimicry | Outcome tracking, unpredictable scenario injection |
| Cold start | Seed with validated traces from test scenarios |
| Embedding quality | Multiple embedding models, human review sampling |

## 10. References

- [CIRIS Covenant 1.0b](https://ciris.ai/covenant/)
- [Coherence Ratchet Overview](https://ciris.ai/coherence-ratchet/)
- [Trace Structure](https://ciris.ai/explore-a-trace/)
