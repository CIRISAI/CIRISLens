# CIRISLens Log Integration Guide

This guide explains how to integrate CIRISBilling, CIRISProxy, and CIRISManager with CIRISLens for centralized log aggregation.

## Prerequisites

1. Generate a service token from the CIRISLens admin interface:
   - Go to https://agents.ciris.ai/lens/admin/
   - Sign in with your @ciris.ai Google account
   - Select the service (e.g., "CIRISBilling") and click "Generate Token"
   - **Copy the token immediately** - it won't be shown again!

## Quick Start

### Step 1: Copy the LogShipper module

Copy `logshipper.py` to your project:

```bash
# From CIRISLens repo
cp sdk/logshipper.py /path/to/your/project/
```

### Step 2: Add the token to your environment

```bash
# In .env or docker-compose.yml
CIRISLENS_TOKEN=svc_your_token_here
```

### Step 3: Initialize in your application

```python
from logshipper import setup_logging
import os

# At application startup
shipper = setup_logging(
    service_name="cirisbilling",  # or "cirisproxy", "cirismanager"
    token=os.environ["CIRISLENS_TOKEN"]
)

# Then use standard Python logging everywhere
import logging
logger = logging.getLogger(__name__)
logger.info("Application started")
```

---

## Integration by Service

### CIRISBilling

**File: `CIRISBilling/app/main.py` or equivalent**

```python
import os
import logging
from logshipper import setup_logging

# Initialize at startup
def init_logging():
    if os.environ.get("CIRISLENS_TOKEN"):
        setup_logging(
            service_name="cirisbilling",
            token=os.environ["CIRISLENS_TOKEN"],
            min_level=logging.INFO
        )
        logging.info("CIRISLens logging enabled")
    else:
        logging.basicConfig(level=logging.INFO)
        logging.warning("CIRISLENS_TOKEN not set, logging locally only")

# Call during startup
init_logging()
```

**Add to `docker-compose.yml`:**
```yaml
services:
  billing:
    environment:
      - CIRISLENS_TOKEN=${CIRISLENS_TOKEN}
```

**Add to `.env`:**
```
CIRISLENS_TOKEN=svc_xxx_your_token_here
```

---

### CIRISProxy

**File: `CIRISProxy/proxy/main.py` or equivalent**

```python
import os
import logging
from logshipper import setup_logging

# At startup
if os.environ.get("CIRISLENS_TOKEN"):
    shipper = setup_logging(
        service_name="cirisproxy",
        token=os.environ["CIRISLENS_TOKEN"],
        min_level=logging.INFO
    )

logger = logging.getLogger("cirisproxy")
```

**For request logging, add context:**

```python
@app.middleware("http")
async def log_requests(request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    response = await call_next(request)

    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code}",
        extra={
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": str(request.url.path),
            "status_code": response.status_code,
            "user_id": request.state.user_id if hasattr(request.state, "user_id") else None
        }
    )

    return response
```

---

### CIRISManager

**File: `CIRISManager/manager/main.py` or equivalent**

```python
import os
import logging
from logshipper import setup_logging

# At startup
shipper = None
if os.environ.get("CIRISLENS_TOKEN"):
    shipper = setup_logging(
        service_name="cirismanager",
        token=os.environ["CIRISLENS_TOKEN"],
        min_level=logging.INFO
    )

logger = logging.getLogger("cirismanager")
```

**For agent lifecycle events:**

```python
def on_agent_started(agent_id: str, template: str):
    logger.info(
        f"Agent {agent_id} started",
        extra={
            "event": "agent_started",
            "agent_id": agent_id,
            "template": template
        }
    )

def on_agent_stopped(agent_id: str, reason: str):
    logger.info(
        f"Agent {agent_id} stopped: {reason}",
        extra={
            "event": "agent_stopped",
            "agent_id": agent_id,
            "reason": reason
        }
    )
```

---

## Best Practices

### 1. Use Structured Events

Always include an `event` field for easy filtering:

```python
logger.info("User logged in", extra={"event": "user_login", "user_id": user.id})
logger.error("Payment failed", extra={"event": "payment_failed", "amount": 99.99})
```

### 2. Include Request Context

For web requests, include `request_id` for tracing:

```python
logger.info("Processing request", extra={
    "request_id": request.headers.get("X-Request-ID"),
    "trace_id": request.headers.get("X-Trace-ID")
})
```

### 3. Use Appropriate Log Levels

- **DEBUG**: Detailed diagnostic info (not shipped by default)
- **INFO**: Normal operations, successful events
- **WARNING**: Unexpected but recoverable situations
- **ERROR**: Failures that need attention
- **CRITICAL**: System-wide failures

### 4. Don't Log Sensitive Data

The LogShipper sends to CIRISLens which has PII redaction, but avoid logging:
- Passwords or tokens
- Full credit card numbers
- Personal addresses
- Private API keys

Use user_id (will be hashed) instead of email/name.

---

## Advanced Configuration

### Environment Variables

```bash
# Required
CIRISLENS_TOKEN=svc_xxx

# Optional
CIRISLENS_SERVICE_NAME=cirisbilling  # Override service name
CIRISLENS_ENDPOINT=https://agents.ciris.ai/lens/api/v1/logs/ingest
```

### Using Environment-Based Setup

```python
from logshipper import from_env

# Reads CIRISLENS_TOKEN and optional CIRISLENS_SERVICE_NAME from env
shipper = from_env(service_name="cirisbilling")
```

### Direct API (without Python logging)

```python
from logshipper import LogShipper

shipper = LogShipper(
    service_name="cirisbilling",
    token=os.environ["CIRISLENS_TOKEN"],
    batch_size=50,          # Flush after 50 logs
    flush_interval=10.0,    # Or every 10 seconds
)

# Log directly
shipper.info("Payment processed", event="payment_completed", user_id="u123", amount=99.99)
shipper.error("Webhook failed", event="webhook_error", url="https://...")

# Manual flush (also happens on shutdown)
shipper.flush()

# Check stats
print(shipper.get_stats())
# {'sent_count': 42, 'error_count': 0, 'buffer_size': 3, 'last_error': None}
```

### Graceful Shutdown

The LogShipper registers an `atexit` handler to flush on shutdown. For explicit control:

```python
# In your shutdown handler
shipper.shutdown()
```

---

## Viewing Logs

1. **CIRISLens Admin**: https://agents.ciris.ai/lens/admin/ → Service Logs tab
2. **Grafana**: Coming soon - dashboards for service log analysis

---

## Troubleshooting

### Logs not appearing?

1. Check the token is valid (not revoked) in admin UI
2. Verify CIRISLENS_TOKEN environment variable is set
3. Check shipper stats: `print(shipper.get_stats())`
4. Look for errors in local console output

### Token rejected (401 error)?

- Token may have been revoked - generate a new one in admin UI
- Check token format: should start with `svc_`

### High memory usage?

- Reduce `batch_size` to flush more frequently
- Check network connectivity to CIRISLens
