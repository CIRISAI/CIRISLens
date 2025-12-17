"""
CIRISLens LogShipper - Drop-in log shipping for CIRIS services.

Copy this file to your project and configure it to send logs to CIRISLens.
Includes circuit breaker and exponential backoff for endpoint resilience.

Usage:
    from logshipper import LogShipper, setup_logging

    # Option 1: As a logging handler (recommended)
    setup_logging(
        service_name="cirisbilling",
        token="svc_xxx",
        endpoint="https://agents.ciris.ai/lens/api/v1/logs/ingest"
    )

    # Then use standard logging
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Payment processed", extra={"user_id": "u123", "amount": 99.99})

    # Option 2: Direct API
    shipper = LogShipper(
        service_name="cirisbilling",
        token="svc_xxx",
        endpoint="https://agents.ciris.ai/lens/api/v1/logs/ingest"
    )
    shipper.info("Payment processed", event="payment_completed", user_id="u123")
    shipper.flush()  # Send buffered logs

    # Option 3: With custom resilience settings
    shipper = LogShipper(
        service_name="cirisbilling",
        token="svc_xxx",
        circuit_failure_threshold=3,    # Open circuit after 3 failures
        circuit_reset_timeout=120.0,    # Try again after 2 minutes
        backoff_initial=2.0,            # Start with 2s backoff
        backoff_max=600.0,              # Max 10 minute backoff
        max_buffer_bytes=50*1024*1024,  # 50MB buffer limit
    )
"""

import atexit
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Try to import resilience module (may not be available if logshipper.py is copied standalone)
try:
    from .resilience import (
        BackoffConfig,
        CircuitBreakerConfig,
        CircuitState,
        ResilientClient,
        ResilientClientConfig,
    )

    RESILIENCE_AVAILABLE = True
except ImportError:
    RESILIENCE_AVAILABLE = False

__version__ = "1.1.0"

# Default CIRISLens log ingestion endpoint
DEFAULT_ENDPOINT = "https://agents.ciris.ai/lens/api/v1/logs/ingest"


class LogShipper:
    """
    Batched log shipper for CIRISLens.

    Buffers logs and sends them in batches to reduce network overhead.
    Thread-safe and handles failures gracefully with circuit breaker
    and exponential backoff.
    """

    def __init__(
        self,
        service_name: str,
        token: str,
        endpoint: str = DEFAULT_ENDPOINT,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        server_id: str | None = None,
        max_retries: int = 3,
        timeout: float = 10.0,
        # Resilience settings
        circuit_failure_threshold: int = 5,
        circuit_reset_timeout: float = 300.0,
        backoff_initial: float = 1.0,
        backoff_max: float = 300.0,
        max_buffer_bytes: int = 100 * 1024 * 1024,  # 100MB
        max_buffer_items: int = 100_000,
        on_circuit_open: "Callable[[], None] | None" = None,
        on_circuit_close: "Callable[[], None] | None" = None,
    ):
        """
        Initialize the LogShipper.

        Args:
            service_name: Name of the service (e.g., "cirisbilling")
            token: Service token from CIRISLens admin
            endpoint: CIRISLens log ingestion endpoint
            batch_size: Max logs to buffer before auto-flush
            flush_interval: Seconds between auto-flushes
            server_id: Optional server identifier (defaults to hostname)
            max_retries: Number of retry attempts on failure
            timeout: HTTP request timeout in seconds
            circuit_failure_threshold: Failures before opening circuit breaker
            circuit_reset_timeout: Seconds before attempting reconnection
            backoff_initial: Initial backoff delay in seconds
            backoff_max: Maximum backoff delay in seconds
            max_buffer_bytes: Maximum buffer size in bytes before dropping logs
            max_buffer_items: Maximum number of log items in buffer
            on_circuit_open: Callback when circuit breaker opens
            on_circuit_close: Callback when circuit breaker closes
        """
        self.service_name = service_name
        self.token = token
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.server_id = server_id or socket.gethostname()
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_buffer_bytes = max_buffer_bytes
        self.max_buffer_items = max_buffer_items

        self._buffer: queue.Queue = queue.Queue()
        self._buffer_bytes = 0
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._flush_thread: threading.Thread | None = None
        self._next_attempt_time: float = 0  # For backoff timing

        # Stats
        self._sent_count = 0
        self._error_count = 0
        self._dropped_count = 0
        self._last_error: str | None = None

        # Initialize resilience client if available
        self._resilient: ResilientClient | None = None
        if RESILIENCE_AVAILABLE:
            config = ResilientClientConfig(
                circuit_breaker=CircuitBreakerConfig(
                    failure_threshold=circuit_failure_threshold,
                    reset_timeout=circuit_reset_timeout,
                ),
                backoff=BackoffConfig(
                    initial_delay=backoff_initial,
                    max_delay=backoff_max,
                ),
            )
            self._resilient = ResilientClient(
                name=f"logshipper-{service_name}",
                config=config,
                on_circuit_open=on_circuit_open,
                on_circuit_close=on_circuit_close,
            )
        else:
            # Fallback: simple exponential backoff state
            self._backoff_attempt = 0
            self._backoff_initial = backoff_initial
            self._backoff_max = backoff_max

        # Start background flush thread
        self._start_flush_thread()

        # Register shutdown handler
        atexit.register(self.shutdown)

    def _start_flush_thread(self):
        """Start the background flush thread."""
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self):
        """Background thread that periodically flushes logs."""
        while not self._shutdown.is_set():
            self._shutdown.wait(self.flush_interval)
            if not self._shutdown.is_set():
                self.flush()

    def _log(
        self,
        level: str,
        message: str,
        event: str | None = None,
        logger_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        **attributes,
    ):
        """Add a log entry to the buffer."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            "server_id": self.server_id,
        }

        if event:
            entry["event"] = event
        if logger_name:
            entry["logger"] = logger_name
        if request_id:
            entry["request_id"] = request_id
        if trace_id:
            entry["trace_id"] = trace_id
        if user_id:
            entry["user_id"] = user_id
        if attributes:
            entry["attributes"] = attributes

        # Estimate entry size
        entry_bytes = len(json.dumps(entry))

        # Check buffer limits
        with self._lock:
            if self._buffer.qsize() >= self.max_buffer_items:
                self._dropped_count += 1
                return  # Drop this log
            if self._buffer_bytes + entry_bytes > self.max_buffer_bytes:
                self._dropped_count += 1
                return  # Drop this log
            self._buffer_bytes += entry_bytes

        self._buffer.put(entry)

        # Auto-flush if buffer is full
        if self._buffer.qsize() >= self.batch_size:
            self.flush()

    def debug(self, message: str, **kwargs):
        """Log a DEBUG message."""
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs):
        """Log an INFO message."""
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log a WARNING message."""
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log an ERROR message."""
        self._log("ERROR", message, **kwargs)

    def critical(self, message: str, **kwargs):
        """Log a CRITICAL message."""
        self._log("CRITICAL", message, **kwargs)

    def flush(self) -> bool:
        """
        Flush buffered logs to CIRISLens.

        Returns:
            True if successful, False otherwise.
        """
        # Check if we should skip due to circuit breaker or backoff
        if self._resilient:
            if not self._resilient.should_attempt():
                return False  # Circuit is open, skip this flush
        else:
            # Fallback: check backoff timing
            if time.time() < self._next_attempt_time:
                return False

        logs = []
        logs_bytes = 0

        # Drain the buffer
        while True:
            try:
                log = self._buffer.get_nowait()
                logs.append(log)
                logs_bytes += len(json.dumps(log))
            except queue.Empty:
                break

        if not logs:
            return True

        # Send logs
        success = self._send_logs(logs)

        if not success:
            # Re-queue failed logs (at the front)
            requeued_bytes = 0
            for log in reversed(logs):
                try:
                    log_bytes = len(json.dumps(log))
                    self._buffer.put_nowait(log)
                    requeued_bytes += log_bytes
                except queue.Full:
                    with self._lock:
                        self._dropped_count += 1
        else:
            # Update buffer bytes on success
            with self._lock:
                self._buffer_bytes = max(0, self._buffer_bytes - logs_bytes)

        return success

    def _send_logs(self, logs: list) -> bool:
        """Send logs to CIRISLens with retry logic and resilience."""
        payload = "\n".join(json.dumps(log) for log in logs)

        for attempt in range(self.max_retries):
            try:
                request = Request(
                    self.endpoint,
                    data=payload.encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/x-ndjson",
                    },
                    method="POST",
                )

                with urlopen(request, timeout=self.timeout) as response:
                    if response.status == 200:
                        with self._lock:
                            self._sent_count += len(logs)

                        # Record success with resilience client
                        if self._resilient:
                            self._resilient.record_success()
                        else:
                            self._backoff_attempt = 0
                            self._next_attempt_time = 0

                        return True

            except HTTPError as e:
                error_msg = f"HTTP {e.code}: {e.reason}"
                with self._lock:
                    self._last_error = error_msg
                    self._error_count += 1

                # Don't retry on auth errors
                if e.code in (401, 403):
                    self._record_failure(error_msg)
                    break

            except URLError as e:
                error_msg = str(e.reason)
                with self._lock:
                    self._last_error = error_msg
                    self._error_count += 1

            except Exception as e:
                error_msg = str(e)
                with self._lock:
                    self._last_error = error_msg
                    self._error_count += 1

            # Exponential backoff between retries
            if attempt < self.max_retries - 1:
                time.sleep(min(2**attempt, 10))  # Cap retry backoff at 10s

        # All retries failed - record failure for circuit breaker
        self._record_failure(self._last_error)
        return False

    def _record_failure(self, error: str | None):
        """Record a failure with the resilience client."""
        if self._resilient:
            self._resilient.record_failure(error)
            self._next_attempt_time = time.time() + self._resilient.get_backoff_delay()
        else:
            # Fallback: simple exponential backoff
            delay = min(
                self._backoff_initial * (2**self._backoff_attempt),
                self._backoff_max,
            )
            self._backoff_attempt += 1
            self._next_attempt_time = time.time() + delay

    def get_stats(self) -> dict:
        """Get shipping statistics including resilience metrics."""
        with self._lock:
            stats = {
                "sent_count": self._sent_count,
                "error_count": self._error_count,
                "dropped_count": self._dropped_count,
                "buffer_size": self._buffer.qsize(),
                "buffer_bytes": self._buffer_bytes,
                "last_error": self._last_error,
            }

        # Add resilience metrics
        if self._resilient:
            resilience = self._resilient.get_metrics()
            stats["circuit_state"] = resilience["circuit"]["state"]
            stats["circuit_failure_count"] = resilience["circuit"]["failure_count"]
            stats["backoff_delay"] = resilience["backoff"]["current_delay"]
            stats["blocked_by_circuit"] = resilience["totals"]["blocked_by_circuit"]
        else:
            stats["circuit_state"] = "n/a"
            stats["backoff_delay"] = max(0, self._next_attempt_time - time.time())

        return stats

    @property
    def circuit_state(self) -> str:
        """Get current circuit breaker state."""
        if self._resilient:
            return self._resilient.circuit_state.value
        return "n/a"

    @property
    def is_healthy(self) -> bool:
        """Check if shipper is in healthy state (circuit closed)."""
        if self._resilient:
            return self._resilient.is_healthy
        return self._next_attempt_time <= time.time()

    def shutdown(self):
        """Gracefully shutdown the shipper."""
        self._shutdown.set()
        self.flush()  # Final flush


class LogShipperHandler(logging.Handler):
    """
    Python logging handler that ships logs to CIRISLens.

    Integrates with standard Python logging so existing code
    works without modification.
    """

    def __init__(self, shipper: LogShipper, min_level: int = logging.INFO):
        """
        Initialize the handler.

        Args:
            shipper: LogShipper instance
            min_level: Minimum log level to ship (default: INFO)
        """
        super().__init__(level=min_level)
        self.shipper = shipper

    def emit(self, record: logging.LogRecord):
        """Emit a log record."""
        try:
            # Extract extra attributes
            attributes = {}
            for key, value in record.__dict__.items():
                if key not in (
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "exc_info",
                    "exc_text",
                    "thread",
                    "threadName",
                    "message",
                    "asctime",
                ):
                    # Only include serializable values
                    if isinstance(value, str | int | float | bool | type(None)):
                        attributes[key] = value
                    elif isinstance(value, list | dict):
                        try:
                            json.dumps(value)  # Test serializability
                            attributes[key] = value
                        except (TypeError, ValueError):
                            pass

            # Extract special fields from attributes
            event = attributes.pop("event", None)
            request_id = attributes.pop("request_id", None)
            trace_id = attributes.pop("trace_id", None)
            user_id = attributes.pop("user_id", None)

            self.shipper._log(
                level=record.levelname,
                message=self.format(record),
                event=event,
                logger_name=record.name,
                request_id=request_id,
                trace_id=trace_id,
                user_id=user_id,
                **attributes,
            )

        except Exception:
            self.handleError(record)


def setup_logging(
    service_name: str,
    token: str,
    endpoint: str = DEFAULT_ENDPOINT,
    min_level: int = logging.INFO,
    also_console: bool = True,
    **shipper_kwargs,
) -> LogShipper:
    """
    Set up Python logging to ship logs to CIRISLens.

    This is the easiest way to integrate - just call this once at startup
    and all your existing logging calls will automatically ship to CIRISLens.

    Args:
        service_name: Name of the service
        token: Service token from CIRISLens admin
        endpoint: CIRISLens endpoint
        min_level: Minimum log level to ship
        also_console: Also log to console (default: True)
        **shipper_kwargs: Additional args passed to LogShipper

    Returns:
        LogShipper instance (for stats/manual flush)

    Example:
        from logshipper import setup_logging
        import logging

        shipper = setup_logging(
            service_name="cirisbilling",
            token=os.environ["CIRISLENS_TOKEN"]
        )

        logger = logging.getLogger(__name__)
        logger.info("Service started")
        logger.error("Payment failed", extra={"user_id": "u123", "event": "payment_failed"})
    """
    # Create shipper
    shipper = LogShipper(
        service_name=service_name,
        token=token,
        endpoint=endpoint,
        **shipper_kwargs,
    )

    # Create handler
    handler = LogShipperHandler(shipper, min_level=min_level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Optionally add console handler
    if also_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root_logger.addHandler(console_handler)

    # Set level if not already set
    if root_logger.level == logging.NOTSET:
        root_logger.setLevel(min_level)

    return shipper


# Convenience for environment-based configuration
def from_env(service_name: str | None = None) -> LogShipper:
    """
    Create a LogShipper from environment variables.

    Environment variables:
        CIRISLENS_SERVICE_NAME: Service name (required if not passed)
        CIRISLENS_TOKEN: Service token (required)
        CIRISLENS_ENDPOINT: API endpoint (optional)

    Args:
        service_name: Override service name from env

    Returns:
        Configured LogShipper instance
    """
    name = service_name or os.environ.get("CIRISLENS_SERVICE_NAME")
    token = os.environ.get("CIRISLENS_TOKEN")
    endpoint = os.environ.get("CIRISLENS_ENDPOINT", DEFAULT_ENDPOINT)

    if not name:
        raise ValueError("service_name required or set CIRISLENS_SERVICE_NAME")
    if not token:
        raise ValueError("CIRISLENS_TOKEN environment variable required")

    return LogShipper(service_name=name, token=token, endpoint=endpoint)
