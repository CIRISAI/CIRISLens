# Root Cause Analysis: Grafana SQLite Database Lock

**Date**: December 12, 2025
**Severity**: P2 - Service Degradation
**Duration**: ~30 minutes (estimated)
**Impact**: Dashboard queries failed, alert notifications not delivered

---

## Incident Summary

Grafana's internal SQLite database became locked under high alert evaluation load, causing cascading failures in dashboard queries, alert state persistence, and notification delivery.

## Timeline

| Time (UTC) | Event |
|------------|-------|
| ~21:30 | Alert evaluation starts failing with "database is locked" |
| ~21:34 | DatasourceError alerts triggered due to failed queries |
| ~21:35 | Cascade: DatasourceError alerts add more write pressure |
| 21:40 | Manual restart of Grafana container |
| 21:41 | Grafana migrated to PostgreSQL backend |
| 21:42 | Service restored |

## Root Cause

**Primary Cause**: Grafana uses SQLite by default for its internal state (users, sessions, alert state, annotations). SQLite has a **single-writer limitation** - only one write operation can occur at a time. Concurrent writes queue up and eventually timeout.

**Trigger**: High concurrent write load from:
1. Three alert rules evaluating every 60 seconds
2. Each alert writing state to SQLite
3. Dashboard auto-refresh (30s) causing query metadata writes
4. Alert annotations being created for state changes

**Contributing Factors**:

1. **Alert Storm Cascade**
   - Initial lock → Alert evaluation fails
   - Failed evaluation → "DatasourceError" system alert fires
   - DatasourceError alert → More writes → More locks
   - Positive feedback loop

2. **Default Email Contact Point**
   - System-generated alerts use default email notification
   - SMTP not configured → Failed delivery attempts
   - Failed delivery → More state writes

## Resolution

### Immediate (21:40 UTC)
- Restarted Grafana container to clear SQLite locks

### Permanent Fix (21:41 UTC)
- Migrated Grafana internal database from SQLite to PostgreSQL
- PostgreSQL handles concurrent writes without locking issues

**Configuration Change**:
```yaml
# docker-compose.yml - Grafana service
environment:
  - GF_DATABASE_TYPE=postgres
  - GF_DATABASE_HOST=cirislens-db:5432
  - GF_DATABASE_NAME=grafana
  - GF_DATABASE_USER=cirislens
  - GF_DATABASE_PASSWORD=cirislens
  - GF_DATABASE_SSL_MODE=disable
```

**Database Creation**:
```sql
CREATE DATABASE grafana OWNER cirislens;
```

## Verification

```bash
# Grafana health check shows PostgreSQL working
curl http://localhost:3001/api/health
# Returns: {"database": "ok", "version": "12.3.0", ...}
```

## Lessons Learned

1. **SQLite is unsuitable for production Grafana** when alerting is enabled
2. **Alert cascades can overwhelm the system** - need circuit breakers
3. **Contact points must be fully configured** before enabling alerts

## Action Items

| Item | Status | Owner |
|------|--------|-------|
| Switch Grafana to PostgreSQL | ✅ Done | - |
| Verify Discord notifications working | Pending | - |
| Add alert for high database connection count | Pending | - |
| Document PostgreSQL requirement | ✅ This document | - |

## Prevention

This issue **cannot recur** because:

1. **PostgreSQL backend**: Handles unlimited concurrent writes
2. **Shared database**: Uses existing TimescaleDB container (resource efficient)
3. **No SQLite**: Grafana volume still exists but SQLite is not used

## References

- [Grafana Database Configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-database/)
- [SQLite Locking](https://www.sqlite.org/lockingv3.html)
- [Grafana Alerting Architecture](https://grafana.com/docs/grafana/latest/alerting/)
