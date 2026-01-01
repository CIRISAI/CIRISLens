# Hash Chain Verification

Verifies the immutability and completeness of an agent's audit trail by checking for sequence gaps or hash mismatches in the cryptographic chain.

## Rationale

Each CIRIS agent trace contains:

- `audit_sequence_number`: Monotonically increasing counter
- `audit_entry_hash`: SHA-256 hash of the audit entry
- `audit_signature`: RSA signature of the audit entry

A complete, tamper-evident audit trail requires:

1. **No sequence gaps**: Every number from 1 to N is present
2. **Correct ordering**: Traces are stored in sequence order
3. **Hash integrity**: Hashes match expected values

**Any break in the hash chain is a CRITICAL alert** requiring immediate investigation.

## How It Works

### 1. Query Ordered Traces

```sql
WITH ordered_traces AS (
    SELECT
        trace_id,
        audit_sequence_number,
        audit_entry_hash,
        LAG(audit_sequence_number) OVER (
            ORDER BY audit_sequence_number
        ) as prev_seq
    FROM covenant_traces
    WHERE agent_id_hash = $1
    ORDER BY audit_sequence_number
)
SELECT *
FROM ordered_traces
WHERE prev_seq IS NOT NULL
  AND audit_sequence_number - prev_seq != 1;
```

### 2. Detect Gaps

For each trace, check if:
```
current_sequence_number == previous_sequence_number + 1
```

### 3. Generate Alerts

Any gap triggers a **CRITICAL** alert:

| Break Type | Description | Severity |
|------------|-------------|----------|
| `sequence_gap` | Missing sequence numbers | Critical |
| `hash_mismatch` | Hash doesn't match expected | Critical (Phase 2) |

## Break Types

### Sequence Gap

```
Expected: 1, 2, 3, 4, 5, 6, 7
Actual:   1, 2, 3,    5, 6, 7  ← Gap at position 4
```

Causes:
- Data loss during transmission
- Database corruption
- Intentional deletion (tampering)
- Agent crash during trace creation

### Hash Mismatch (Phase 2)

```
Trace N contains:
  prev_hash: abc123...

Trace N-1 actually has:
  hash: def456...  ← Mismatch!
```

Causes:
- Tampering with historical traces
- Hash algorithm mismatch
- Data corruption

## Example Alert

```json
{
  "alert_id": "c3d4e5f6-...",
  "severity": "critical",
  "detection_mechanism": "hash_chain",
  "agent_id_hash": "ghi789...",
  "domain": null,
  "metric": "hash_chain_integrity",
  "value": 3.0,
  "baseline": 0.0,
  "deviation": "3 breaks",
  "evidence_traces": ["trace-001", "trace-005", "trace-012"],
  "recommended_action": "CRITICAL: 3 hash chain breaks detected. This may indicate tampering or data loss. Immediate investigation required."
}
```

## Investigation Steps

1. **Identify Gap Location**: Where in the sequence is the gap?
2. **Check Timestamps**: When were the surrounding traces created?
3. **Review Agent Logs**: Was the agent restarted or crashed?
4. **Check Network/DB**: Were there connectivity issues?
5. **Audit Access Logs**: Who accessed the database?
6. **Verify Agent Health**: Is the agent currently functioning?

## Severity

**All hash chain breaks are CRITICAL** because they indicate:

- Potential tampering with audit trail
- Data integrity issues
- Possible security breach

There is no "warning" level for hash chain issues.

## Pure Python Verification

For verifying traces in memory (without database):

```python
from api.analysis.coherence_ratchet import CoherenceRatchetAnalyzer

traces = [
    {"trace_id": "t1", "audit_sequence_number": 1},
    {"trace_id": "t2", "audit_sequence_number": 2},
    {"trace_id": "t4", "audit_sequence_number": 4},  # Gap!
]

breaks = CoherenceRatchetAnalyzer.verify_trace_hash_chain(traces)
# Returns: [HashChainBreak(break_type="sequence_gap", trace_id="t4", expected_seq=3, actual_seq=4)]
```

## Scheduler Configuration

Hash chain verification runs more frequently than other checks:

| Check | Interval | Rationale |
|-------|----------|-----------|
| Hash chain | 1 hour | Integrity is critical |
| Other checks | 24 hours | Statistical analysis |

## API Usage

```bash
# Check hash chain for specific agent
curl /api/v1/covenant/coherence-ratchet/verify-chain?agent_id_hash=abc123

# Get all hash chain alerts
curl /api/v1/covenant/coherence-ratchet/alerts?mechanism=hash_chain
```

## Prevention

To prevent hash chain breaks:

1. **Reliable Transport**: Use guaranteed delivery for trace shipping
2. **Buffering**: Buffer traces during network issues
3. **Checksums**: Verify trace integrity on receipt
4. **Monitoring**: Alert on missing traces quickly
5. **Backups**: Maintain redundant trace storage

## Related

- [Audit Trail Requirements](../../FSD/trace_format_specification.md#audit-fields)
- [Ed25519 Signature Verification](../../CLAUDE.md#covenant-api-endpoints)
