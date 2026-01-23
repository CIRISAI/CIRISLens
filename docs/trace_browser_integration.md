# Trace Browser Integration Guide

Build the `ciris.ai/explore-a-trace` interactive trace explorer using the CIRISLens Trace Repository API.

## API Base URL

```
Production: https://lens.ciris-services-1.ai/api/v1/covenant/repository
```

No authentication required for public endpoints.

## Endpoints

### 1. List Public Traces

```
GET /traces?limit=10&offset=0
```

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Max results (1-1000) |
| `offset` | int | 0 | Pagination offset |
| `domain` | string | - | Filter by DSDMA domain |
| `cognitive_state` | string | - | Filter: work, dream, play, solitude |
| `min_plausibility` | float | - | CSDMA score >= value |
| `max_plausibility` | float | - | CSDMA score <= value |
| `conscience_passed` | bool | - | Filter by conscience check |
| `action_overridden` | bool | - | Filter by override status |
| `fragility_flag` | bool | - | Filter by IDMA fragility |
| `start_time` | ISO8601 | - | Start of time range |
| `end_time` | ISO8601 | - | End of time range |

**Example Request:**
```bash
curl "https://lens.ciris-services-1.ai/api/v1/covenant/repository/traces?limit=5"
```

**Response Structure:**
```json
{
  "traces": [
    {
      "trace_id": "trace-th_seed_xxx",
      "timestamp": "2026-01-23T01:09:22Z",
      "agent": {
        "name": "Scout",
        "id_hash": "8a1db462be753774",
        "domain": "Scout"
      },
      "thought": {
        "thought_id": "th_seed_xxx",
        "type": "standard",
        "depth": 0,
        "cognitive_state": "work"
      },
      "action": {
        "selected": "SPEAK",
        "success": true,
        "was_overridden": false,
        "rationale": "The user's question about..."
      },
      "scores": {
        "csdma_plausibility": 0.9,
        "dsdma_alignment": 0.9,
        "idma_k_eff": 1.0,
        "idma_fragility": true,
        "idma_phase": "rigidity"
      },
      "conscience": {
        "passed": true,
        "entropy_passed": true,
        "coherence_passed": true,
        "optimization_veto_passed": true,
        "epistemic_humility_passed": true
      },
      "dma_results": {
        "csdma": { "reasoning": "...", "plausibility_score": 0.9 },
        "dsdma": { "reasoning": "...", "domain": "Scout", "alignment": 0.9 },
        "pdma": { "stakeholders": "...", "conflicts": "...", "reasoning": "..." },
        "idma": { "reasoning": "...", "k_eff": 1.0, "phase": "rigidity" }
      },
      "resources": {
        "tokens_total": 203991,
        "cost_cents": 2.04,
        "models_used": ["meta-llama/llama-4-maverick-17b"]
      }
    }
  ],
  "pagination": {
    "total": 1,
    "limit": 5,
    "offset": 0,
    "has_more": false
  }
}
```

### 2. Get Single Trace

```
GET /traces/{trace_id}
```

Returns the same structure as a single trace from the list endpoint.

### 3. Get Statistics

```
GET /statistics?domain=Scout
```

**Query Parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `domain` | string | Filter by domain |
| `start_time` | ISO8601 | Start of time range |
| `end_time` | ISO8601 | End of time range |

**Response:**
```json
{
  "totals": {
    "traces": 100,
    "agents": 5,
    "domains": 3
  },
  "scores": {
    "csdma_plausibility": { "mean": 0.87 },
    "dsdma_alignment": { "mean": 0.82 },
    "idma_k_eff": { "mean": 1.2 }
  },
  "conscience": {
    "pass_rate": 0.97,
    "override_rate": 0.02
  },
  "actions": {
    "distribution": {
      "SPEAK": 65,
      "OBSERVE": 35
    }
  },
  "fragility": {
    "fragile_rate": 0.15
  }
}
```

## UI Component Structure

### Recommended Layout

```
+------------------------------------------------------------------+
|  CIRIS Trace Explorer                              [Filter Panel] |
+------------------------------------------------------------------+
|                                                                   |
|  +-- Trace List (left 30%) --+  +-- Trace Detail (right 70%) --+ |
|  |                           |  |                               | |
|  | [Trace Card 1]            |  |  Agent: Scout                 | |
|  |   Scout - SPEAK           |  |  Action: SPEAK (success)      | |
|  |   CSDMA: 0.9 | DSDMA: 0.9 |  |                               | |
|  |   Conscience: PASSED      |  |  +-- Score Gauges -----------+| |
|  |                           |  |  | CSDMA [====90%====]       || |
|  | [Trace Card 2]            |  |  | DSDMA [====90%====]       || |
|  |   ...                     |  |  | IDMA  [===100%====]       || |
|  |                           |  |  +---------------------------+| |
|  +---------------------------+  |                               | |
|                                 |  +-- DMA Analysis Tabs ------+| |
|                                 |  | [CSDMA] [DSDMA] [PDMA] [IDMA]|
|                                 |  |                           || |
|                                 |  | Common Sense Analysis:    || |
|                                 |  | The thought is plausible  || |
|                                 |  | because...                || |
|                                 |  +---------------------------+| |
|                                 |                               | |
|                                 |  +-- Conscience Checks ------+| |
|                                 |  | Entropy:    PASSED        || |
|                                 |  | Coherence:  PASSED        || |
|                                 |  | Opt Veto:   PASSED        || |
|                                 |  | Epistemic:  PASSED        || |
|                                 |  +---------------------------+| |
|                                 +-------------------------------+ |
+------------------------------------------------------------------+
```

### Key UI Elements

#### 1. Score Gauges
Display scores as visual gauges (0-1 scale):

```jsx
// React example
function ScoreGauge({ label, value, threshold = 0.7 }) {
  const color = value >= threshold ? 'green' : value >= 0.5 ? 'yellow' : 'red';
  return (
    <div className="score-gauge">
      <label>{label}</label>
      <div className="gauge-bar">
        <div
          className={`gauge-fill ${color}`}
          style={{ width: `${value * 100}%` }}
        />
      </div>
      <span>{(value * 100).toFixed(0)}%</span>
    </div>
  );
}
```

#### 2. Conscience Check Badges
Show pass/fail status with icons:

```jsx
function ConscienceCheck({ name, passed }) {
  return (
    <div className={`conscience-badge ${passed ? 'passed' : 'failed'}`}>
      {passed ? '✓' : '✗'} {name}
    </div>
  );
}
```

#### 3. DMA Reasoning Expandable Sections
Each DMA has detailed reasoning that can be expanded:

```jsx
function DMASection({ name, data }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="dma-section">
      <button onClick={() => setExpanded(!expanded)}>
        {name} Analysis {expanded ? '▼' : '▶'}
      </button>
      {expanded && (
        <div className="dma-content">
          <p><strong>Reasoning:</strong> {data.reasoning}</p>
          {data.stakeholders && (
            <p><strong>Stakeholders:</strong> {data.stakeholders}</p>
          )}
          {data.conflicts && (
            <p><strong>Conflicts:</strong> {data.conflicts}</p>
          )}
        </div>
      )}
    </div>
  );
}
```

#### 4. Action Rationale Card
Highlight the final decision:

```jsx
function ActionCard({ action }) {
  return (
    <div className={`action-card ${action.success ? 'success' : 'failed'}`}>
      <h3>Action: {action.selected}</h3>
      <p className="rationale">{action.rationale}</p>
      {action.was_overridden && (
        <span className="override-badge">OVERRIDDEN</span>
      )}
    </div>
  );
}
```

## Data Field Reference

### Actions (action.selected)
- `SPEAK` - Generate response
- `OBSERVE` - Gather more information
- `MEMORIZE` - Store information
- `DEFER` - Escalate to human/WA
- `REJECT` - Decline to act

### Cognitive States (thought.cognitive_state)
- `work` - Active task processing
- `dream` - Background processing
- `play` - Exploratory mode
- `solitude` - Self-reflection

### IDMA Phases (scores.idma_phase)
- `rigidity` - Single source, high fragility
- `flexibility` - Multiple sources emerging
- `corroboration` - Well-corroborated reasoning

### DMA Types
| DMA | Purpose | Key Fields |
|-----|---------|------------|
| CSDMA | Common Sense | plausibility_score, reasoning, flags |
| DSDMA | Domain-Specific | domain, alignment, reasoning, flags |
| PDMA | Ethical/Stakeholder | stakeholders, conflicts, reasoning |
| IDMA | Information Source | k_eff, correlation_risk, phase, sources_identified |

## PII Handling

All traces have PII automatically scrubbed. You'll see placeholders like:
- `[PERSON_1]`, `[PERSON_2]` - Names
- `[ORG_1]`, `[ORG_2]` - Organizations
- `[EMAIL]`, `[PHONE]` - Contact info
- `[URL]` - URLs
- `[GPE_1]` - Locations

Display these as-is; they maintain narrative coherence while protecting privacy.

## Example: Fetch and Display Traces

```javascript
// Vanilla JS example
async function loadTraces(filters = {}) {
  const params = new URLSearchParams({
    limit: 10,
    ...filters
  });

  const response = await fetch(
    `https://lens.ciris-services-1.ai/api/v1/covenant/repository/traces?${params}`
  );

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

// Usage
const { traces, pagination } = await loadTraces({
  domain: 'Scout',
  conscience_passed: true
});

traces.forEach(trace => {
  console.log(`${trace.agent.name}: ${trace.action.selected}`);
  console.log(`  CSDMA: ${trace.scores.csdma_plausibility}`);
  console.log(`  Conscience: ${trace.conscience.passed ? 'PASSED' : 'FAILED'}`);
});
```

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| 200 | Success | Display data |
| 400 | Bad request | Show validation error |
| 404 | Trace not found | Show "not found" message |
| 500 | Server error | Show retry option |

## Rate Limits

Public access: 20 requests/minute

Implement client-side caching and debouncing for filter changes.

## Mobile Considerations

- Collapse DMA sections by default
- Use bottom sheet for trace details
- Swipe between traces
- Simplify score display to icons

## Accessibility

- Use semantic HTML for screen readers
- Provide text alternatives for score gauges
- Ensure color is not the only indicator (use icons)
- Support keyboard navigation between traces
