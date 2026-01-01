# Cross-Agent Divergence Detection

Detects agents whose DMA (Decision Making Algorithm) scores diverge significantly from their domain population using z-score statistical analysis.

## Rationale

Agents operating in the same domain and facing similar scenarios should produce similar plausibility and alignment scores. An agent with significantly different scores may be:

- Misconfigured
- Operating on different data
- Compromised
- Experiencing model drift

## How It Works

### 1. Data Collection

The analyzer queries verified traces from the past N days (default: 7):

```sql
SELECT
    agent_id_hash,
    dsdma_domain,
    AVG(csdma_plausibility_score) as avg_plausibility,
    AVG(dsdma_domain_alignment) as avg_alignment,
    AVG(coherence_level) as avg_coherence
FROM covenant_traces
WHERE timestamp > NOW() - INTERVAL '7 days'
  AND signature_verified = TRUE
GROUP BY agent_id_hash, dsdma_domain
```

### 2. Domain Stratification

Results are grouped by domain (e.g., "Datum", "Sage") because different domains have different baseline expectations:

```sql
SELECT
    dsdma_domain,
    AVG(avg_plausibility) as domain_plausibility,
    STDDEV(avg_plausibility) as std_plausibility,
    ...
FROM agent_scores
GROUP BY dsdma_domain
HAVING COUNT(*) >= 3  -- Minimum agents per domain
```

### 3. Z-Score Calculation

For each agent, calculate the z-score relative to their domain:

```
z = |agent_score - domain_mean| / domain_std_dev
```

### 4. Alert Generation

| Z-Score | Severity | Interpretation |
|---------|----------|----------------|
| > 2σ | Warning | Unusual, warrants review |
| > 3σ | Critical | Highly unusual, investigate immediately |

## Metrics Analyzed

| Metric | Source | Description |
|--------|--------|-------------|
| `csdma_plausibility_score` | Common Sense DMA | How plausible is the agent's reasoning? (0-1) |
| `dsdma_domain_alignment` | Domain-Specific DMA | How well aligned with domain expectations? (0-1) |
| `coherence_level` | Epistemic tracking | Internal reasoning coherence (0-1) |

## Minimum Requirements

To reduce false positives from small sample sizes:

| Requirement | Value | Rationale |
|-------------|-------|-----------|
| Min traces per agent | 10 | Statistical significance |
| Min agents per domain | 3 | Meaningful comparison |

## Example Alert

```json
{
  "alert_id": "a1b2c3d4-...",
  "severity": "warning",
  "detection_mechanism": "cross_agent_divergence",
  "agent_id_hash": "abc123...",
  "domain": "Datum",
  "metric": "csdma_plausibility_score",
  "value": 0.45,
  "baseline": 0.82,
  "deviation": "2.3σ",
  "recommended_action": "Agent shows 2.3σ divergence in csdma_plausibility_score. Review traces to determine if behavior is legitimate."
}
```

## Investigation Steps

When you receive a cross-agent divergence alert:

1. **Review Recent Traces**: Examine the agent's recent reasoning traces
2. **Check Context**: Was the agent handling unusual scenarios?
3. **Compare Peers**: Look at what other agents in the domain are doing
4. **Check Configuration**: Verify the agent's configuration matches expectations
5. **Review Timeline**: When did the divergence start?

## False Positive Causes

- Agent handling edge cases or unusual scenarios
- Domain has heterogeneous task distribution
- Recent legitimate configuration change
- Small sample size (though mitigated by minimums)

## Configuration

```python
# In CoherenceRatchetAnalyzer
Z_SCORE_WARNING = 2.0      # Warning threshold
Z_SCORE_CRITICAL = 3.0     # Critical threshold
MIN_TRACES_PER_AGENT = 10  # Minimum sample size
MIN_AGENTS_PER_DOMAIN = 3  # Minimum comparison group
```

## API Usage

```bash
# Run detection manually
curl -X POST /api/v1/covenant/coherence-ratchet/run

# Get cross-agent alerts only
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=cross_agent_divergence
```

## Grafana Queries

```sql
-- Average divergence by domain
SELECT
    dsdma_domain,
    AVG(ABS(csdma_plausibility_score - domain_avg)) / NULLIF(domain_std, 0) as avg_z_score
FROM covenant_traces t
JOIN domain_stats d ON t.dsdma_domain = d.domain
GROUP BY dsdma_domain
```
