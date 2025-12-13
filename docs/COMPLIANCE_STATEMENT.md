# CIRIS Data Retention & Compliance Statement

*Last Updated: December 2025*

This document describes the data retention policies, archival procedures, and compliance measures implemented across the CIRIS infrastructure.

---

## Executive Summary

CIRIS implements a **privacy-by-design** architecture where:

- **No conversation content is ever stored** - We do not retain, log, or archive the content of user messages or AI responses
- **Minimal data collection** - Only data strictly necessary for billing, fraud prevention, and regulatory compliance is retained
- **Automatic data lifecycle management** - All operational data is automatically compressed and deleted according to defined retention periods
- **Regulatory archives are separate** - Financial records required for compliance are archived separately with appropriate encryption and access controls

---

## CIRISLens Observability Data

### What CIRISLens Collects

CIRISLens is our observability platform that monitors system health and performance. It collects:

| Data Type | Description | Contains User Data? |
|-----------|-------------|---------------------|
| **Metrics** | System performance counters (CPU, memory, request latency) | No |
| **Traces** | Request timing and flow through services | No (only request IDs) |
| **Service Logs** | Operational logs from Billing, Proxy, Manager services | No (PII is redacted) |

### What CIRISLens Does NOT Collect

- Message content or conversation history
- User-generated text of any kind
- Personal information (names, addresses, phone numbers)
- Payment card details (handled by Stripe/Google Play directly)

### Retention Periods (Verified in Production)

| Table | Retention | Compression | Purpose |
|-------|-----------|-------------|---------|
| `agent_metrics` | **30 days** | After 7 days | System performance monitoring |
| `agent_logs` | **14 days** | After 7 days | Debugging and incident response |
| `agent_traces` | **14 days** | After 7 days | Request flow analysis |
| `service_logs` | **90 days** | After 7 days | Service audit trail |

### Pre-Computed Aggregates

For long-term trend analysis without raw data:

| Aggregate | Granularity | Retention |
|-----------|-------------|-----------|
| `metrics_hourly` | 1-hour buckets | 90 days |
| `metrics_daily` | 1-day buckets | 1 year |

### Enforcement Mechanism

Retention is enforced automatically by **TimescaleDB background jobs**:

- **Compression jobs** run every 12 hours to compress data older than 7 days
- **Retention jobs** run daily to drop chunks exceeding the retention period
- **No manual intervention required** - data lifecycle is fully automated

---

## CIRISBilling Financial Records

### What CIRISBilling Stores

CIRISBilling maintains financial records required for:
- Tax compliance
- Fraud prevention
- Audit trails
- Dispute resolution

| Data Type | Description | Sensitive? |
|-----------|-------------|------------|
| **Account records** | OAuth provider ID, email, balance | Yes (encrypted) |
| **Credit transactions** | Purchase records, amounts, timestamps | Yes |
| **Charge transactions** | Usage records, amounts, timestamps | Yes |
| **Credit checks** | Authorization requests/responses | No |
| **LLM usage logs** | Token counts, costs (for margin analysis) | No |
| **Admin audit logs** | Administrative actions | No |

### What CIRISBilling Does NOT Store

- **Conversation content** - Never stored, logged, or transmitted to billing
- **Message text** - Only correlation IDs (e.g., `message_id: "msg-123"`)
- **User-generated content** - Zero retention policy
- **Payment card numbers** - Handled by payment processors (Stripe, Google Play)
- **Passwords** - OAuth-only authentication

### 10-Year Regulatory Archive

To comply with the **EU AI Act** and financial audit requirements, CIRISBilling archives billing records for 10 years:

**Archive Process:**
1. Monthly cron job runs on the 2nd of each month at 03:00 UTC
2. Previous month's data exported to compressed Parquet format
3. Uploaded to AWS S3 with SHA-256 checksums
4. Manifest file documents all archived files

**S3 Lifecycle (Cost Optimization):**

| Period | Storage Class | Purpose |
|--------|---------------|---------|
| 0-90 days | S3 Intelligent Tiering | Recent access possible |
| 90-365 days | Glacier Instant Retrieval | Audit access within hours |
| 1-10 years | Glacier Deep Archive | Long-term compliance storage |
| 10+ years | **Deleted** | Regulatory requirement met |

**Archived Tables:**
- `credits` - All credit additions (purchases, grants, refunds)
- `charges` - All credit deductions (usage)
- `google_play_purchases` - Mobile payment records
- `llm_usage_logs` - Provider cost tracking
- `admin_audit_logs` - Administrative actions
- `credit_checks` - Authorization audit trail
- `accounts` - Monthly snapshot (for account state at archive time)

---

## Data Minimization

### Principle

We follow the GDPR principle of **data minimization** - collecting only what is strictly necessary and retaining it only as long as required.

### Implementation

| System | Data Collected | Justification |
|--------|----------------|---------------|
| **CIRISLens** | System metrics, service logs | Operational monitoring, incident response |
| **CIRISBilling** | Account IDs, transaction amounts | Billing, fraud prevention, compliance |
| **CIRISAgent** | None persisted | Stateless message processing |

### PII Handling

- **Email addresses**: Stored for account identification; hashed in logs
- **OAuth IDs**: Provider-supplied identifiers only
- **IP addresses**: Logged for fraud detection; auto-deleted per retention policy
- **User content**: **Never stored** - processed in-memory and discarded

---

## Compliance Framework

### Regulations Addressed

| Regulation | Requirement | Implementation |
|------------|-------------|----------------|
| **GDPR** | Data minimization, right to deletion | Automatic retention, deletion on request |
| **EU AI Act** | Record-keeping for AI systems | 10-year billing archive |
| **SOX** | Financial record retention | 10-year archive with audit trail |
| **PCI DSS** | No card data storage | Payment processors handle all card data |

### Audit Capabilities

- All administrative actions logged with user, timestamp, and changes
- Archive manifests include SHA-256 checksums for integrity verification
- Compression and retention jobs tracked in TimescaleDB job history

### Right to Deletion (GDPR Article 17)

Users may request deletion of their data:

1. **Immediate deletion**: Account data, balances, preferences
2. **Anonymization**: Transaction records anonymized (account_id replaced with hash)
3. **Archive exception**: Regulatory archives retained with anonymized records per legal requirements

---

## Verification

### Production Status

All retention policies are actively enforced. To verify:

```sql
-- View active TimescaleDB policies
SELECT hypertable_name, proc_name, config
FROM timescaledb_information.jobs
WHERE proc_name LIKE 'policy_%';

-- Results (December 2025):
-- agent_metrics | policy_retention | {"drop_after": "30 days"}
-- agent_logs    | policy_retention | {"drop_after": "14 days"}
-- agent_traces  | policy_retention | {"drop_after": "14 days"}
-- service_logs  | policy_retention | {"drop_after": "90 days"}
```

### Archive Verification

```bash
# List archived months
aws s3 ls s3://ciris-billing-archive/billing-archive/

# Verify manifest
aws s3 cp s3://ciris-billing-archive/billing-archive/2025/11/manifest.json - | jq .
```

---

## Contact

For data protection inquiries or deletion requests:

- **Data Protection Officer**: privacy@ciris.ai
- **Technical Questions**: security@ciris.ai

---

*This document is version-controlled and updated with each policy change.*
