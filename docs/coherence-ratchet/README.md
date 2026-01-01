# Coherence Ratchet Detection System

The Coherence Ratchet is CIRISLens's anomaly detection system for identifying potentially misaligned agent behavior through statistical analysis of Ed25519-signed reasoning traces.

## Overview

CIRIS agents emit cryptographically signed reasoning traces that document their decision-making process. The Coherence Ratchet analyzes these traces to detect statistical anomalies that may indicate:

- Configuration drift
- Compromised agents
- Training/fine-tuning issues
- Ethical alignment problems

**Important**: Detection is triage, not verdict. Anomalies warrant human investigation but don't prove misalignment.

## Detection Mechanisms

| Mechanism | Purpose | Documentation |
|-----------|---------|---------------|
| [Cross-Agent Divergence](cross-agent-divergence.md) | Detect agents whose scores differ from peers | Z-score analysis by domain |
| [Intra-Agent Consistency](intra-agent-consistency.md) | Detect self-contradictory reasoning | Action variance tracking |
| [Hash Chain Verification](hash-chain-verification.md) | Verify audit trail integrity | Sequence gap detection |
| [Temporal Drift](temporal-drift.md) | Track behavioral changes over time | Daily score comparison |
| [Conscience Override](conscience-override.md) | Monitor ethical intervention rates | Override pattern analysis |

## Alert Severity Levels

| Level | Meaning | Response |
|-------|---------|----------|
| **Warning** | Statistical anomaly detected | Review within 24 hours |
| **Critical** | Severe deviation or integrity issue | Immediate investigation |

## Detection Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Cross-agent divergence (z-score) | > 2σ | > 3σ |
| Daily score drift | > 15% | > 25% |
| Conscience override rate | > 2x domain avg | > 3x domain avg |
| Hash chain gaps | - | Any gap |

## API Endpoints

```
GET    /api/v1/covenant/coherence-ratchet/alerts
POST   /api/v1/covenant/coherence-ratchet/run
PUT    /api/v1/covenant/coherence-ratchet/alerts/{id}/acknowledge
PUT    /api/v1/covenant/coherence-ratchet/alerts/{id}/resolve
GET    /api/v1/covenant/coherence-ratchet/stats
```

## Scheduler Configuration

Detection jobs run on configurable intervals:

| Job | Default Interval | Rationale |
|-----|------------------|-----------|
| Cross-agent divergence | 24 hours | Requires population data |
| Temporal drift | 24 hours | Daily comparison |
| Hash chain verification | 1 hour | Integrity is critical |
| Conscience overrides | 24 hours | Pattern analysis |
| Intra-agent consistency | 24 hours | Requires trace history |

## Database Tables

| Table | Purpose |
|-------|---------|
| `covenant_traces` | Stores signed reasoning traces with denormalized DMA scores |
| `coherence_ratchet_alerts` | Persisted anomaly alerts with lifecycle tracking |
| `covenant_public_keys` | Ed25519 public keys for signature verification |

## Grafana Dashboard

The "Coherence Ratchet Detection" dashboard (`dashboards/coherence_ratchet.json`) provides:

- Alert overview (warning/critical counts, hash chain breaks)
- Cross-agent divergence time series with threshold lines
- Temporal drift analysis with significant change highlighting
- Conscience override rates by agent/domain
- Recent anomaly alerts table

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  CIRIS Agents   │────▶│  covenant_traces │────▶│   Analyzer      │
│  (Ed25519 sigs) │     │  (TimescaleDB)   │     │  (5 mechanisms) │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                        ┌──────────────────┐              ▼
                        │  Scheduler       │     ┌─────────────────┐
                        │  (background)    │────▶│  Alerts Table   │
                        └──────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │  Grafana        │
                                                 │  Dashboard      │
                                                 └─────────────────┘
```

## Code Location

- **Analysis Module**: `api/analysis/coherence_ratchet.py`
- **Scheduler**: `api/analysis/scheduler.py`
- **API Endpoints**: `api/covenant_api.py`
- **Database Schema**: `sql/011_covenant_traces.sql`
- **Dashboard**: `dashboards/coherence_ratchet.json`

## Related Documentation

- [FSD: Trace Format Specification](../../FSD/trace_format_specification.md)
- [FSD: Coherence Ratchet Detection](../../FSD/coherence_ratchet_detection.md)
