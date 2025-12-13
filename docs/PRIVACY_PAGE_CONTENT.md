# Privacy & Data Retention

*For ciris.ai/privacy*

---

## Our Commitment

**We don't store your conversations.** Period.

When you interact with CIRIS AI agents, your messages are processed in real-time and immediately discarded. We have no database of your conversations, no logs of what you said, and no way to retrieve past interactions.

---

## What We Do Store

### Billing Data (Required for Service)

| Data | Purpose | Retention |
|------|---------|-----------|
| Account email | Identify your account | Until you delete your account |
| Transaction history | Billing records, refunds | 10 years (regulatory requirement) |
| Usage counts | Track your credits | 10 years |

### System Monitoring (No User Content)

| Data | Purpose | Retention |
|------|---------|-----------|
| Performance metrics | Keep services running | 30 days |
| Error logs | Fix bugs | 14-90 days |
| Request IDs | Troubleshoot issues | 14 days |

**Note:** Monitoring data contains only system information (timestamps, response times, error codes) - never your messages or personal information.

---

## What We Never Store

- Your messages to AI agents
- AI responses to you
- Conversation history
- Payment card numbers (handled by Stripe/Google Play)
- Your location
- Your device information

---

## Regulatory Compliance

We maintain financial records for 10 years as required by:

- **EU AI Act** - Record-keeping requirements for AI systems
- **Financial regulations** - Tax and audit compliance

These archives contain only transaction records (dates, amounts, account IDs) - never conversation content.

---

## Your Rights

### Delete Your Data

Request deletion of your account and all associated data at any time. Contact privacy@ciris.ai.

### Export Your Data

Request a copy of all data we hold about you in machine-readable format.

### Access Your Data

View your transaction history and account information in your account dashboard.

---

## Technical Implementation

Our data retention is enforced automatically:

- **TimescaleDB** automatically deletes operational data after the retention period
- **AWS S3 Glacier** stores regulatory archives with automatic 10-year expiration
- **No manual intervention** required - data lifecycle is fully automated

---

## Questions?

- **Privacy inquiries**: privacy@ciris.ai
- **Data deletion requests**: privacy@ciris.ai
- **Technical questions**: security@ciris.ai

---

*Last updated: December 2025*
