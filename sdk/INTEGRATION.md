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
- Reduce `max_buffer_bytes` if buffering during outages

---

## Resilience Features (v1.1.0+)

The LogShipper now includes circuit breaker and exponential backoff patterns to prevent bandwidth waste when CIRISLens is unavailable.

### Circuit Breaker

When CIRISLens becomes unavailable, the circuit breaker:
1. Opens after 5 consecutive failures (configurable)
2. Blocks all shipping attempts while open (saves bandwidth)
3. Transitions to half-open after 5 minutes to test recovery
4. Closes again on successful delivery

### Exponential Backoff

Between failures, the shipper backs off exponentially:
- 1s → 2s → 4s → 8s → ... → 5 minutes max

### Buffer Limits

Logs are buffered during outages with configurable limits:
- `max_buffer_bytes`: Maximum buffer size (default 100MB)
- `max_buffer_items`: Maximum log count (default 100,000)
- Oldest logs are dropped when limits are exceeded

### Configuration

```python
from logshipper import LogShipper

shipper = LogShipper(
    service_name="myservice",
    token=os.environ["CIRISLENS_TOKEN"],

    # Circuit breaker settings
    circuit_failure_threshold=5,    # Open after 5 failures
    circuit_reset_timeout=300.0,    # Try again after 5 minutes

    # Backoff settings
    backoff_initial=1.0,            # Start at 1 second
    backoff_max=300.0,              # Max 5 minutes between attempts

    # Buffer limits
    max_buffer_bytes=100*1024*1024, # 100MB buffer limit
    max_buffer_items=100_000,       # Or 100k log entries

    # Callbacks for monitoring
    on_circuit_open=lambda: print("Circuit opened - shipping paused"),
    on_circuit_close=lambda: print("Circuit closed - shipping resumed"),
)
```

### Monitoring Circuit State

```python
# Check if shipper is healthy
if shipper.is_healthy:
    print("Shipping normally")
else:
    print(f"Circuit is {shipper.circuit_state}")

# Get detailed stats
stats = shipper.get_stats()
print(f"Circuit: {stats['circuit_state']}")
print(f"Sent: {stats['sent_count']}, Errors: {stats['error_count']}")
print(f"Dropped: {stats['dropped_count']}")
print(f"Buffer: {stats['buffer_size']} items, {stats['buffer_bytes']} bytes")
print(f"Blocked by circuit: {stats.get('blocked_by_circuit', 0)}")
```

### Using the Resilience Module Directly

For custom HTTP clients, use the standalone resilience patterns:

```python
from sdk.resilience import ResilientClient, ResilientClientConfig

client = ResilientClient(
    name="my-api-client",
    config=ResilientClientConfig(
        circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
        backoff=BackoffConfig(initial_delay=0.5, max_delay=60.0),
    ),
)

def call_api():
    if not client.should_attempt():
        return None  # Circuit is open

    try:
        result = requests.post(url, data)
        client.record_success()
        return result
    except Exception as e:
        client.record_failure(str(e))
        time.sleep(client.get_backoff_delay())
        raise
```
