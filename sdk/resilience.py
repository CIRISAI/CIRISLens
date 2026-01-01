"""
Resilience patterns for CIRISLens SDK.

Provides reusable circuit breaker, exponential backoff, and rate limiting
patterns for any component that needs endpoint resilience.

Usage:
    from resilience import ResilientClient

    client = ResilientClient(
        name="log-shipper",
        circuit_breaker=CircuitBreakerConfig(failure_threshold=5),
        backoff=BackoffConfig(initial_delay=1.0, max_delay=300.0),
    )

    async with client.execute() as attempt:
        if attempt.should_proceed:
            result = await send_request()
            attempt.record_success()
        else:
            # Circuit is open, skip this attempt
            pass
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

__version__ = "1.0.0"

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests flow through
    OPEN = "open"  # Failing, requests are blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""

    failure_threshold: int = 5  # Failures before opening circuit
    reset_timeout: float = 300.0  # Seconds before trying again (5 min)
    half_open_max_calls: int = 1  # Test calls allowed in half-open state
    success_threshold: int = 1  # Successes needed to close circuit


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff."""

    initial_delay: float = 1.0  # Starting delay in seconds
    max_delay: float = 300.0  # Maximum delay (5 minutes)
    multiplier: float = 2.0  # Exponential multiplier
    jitter: float = 0.1  # Random jitter factor (0-1)


@dataclass
class BufferConfig:
    """Configuration for local buffering."""

    max_size_bytes: int = 100 * 1024 * 1024  # 100MB default
    max_items: int = 100_000  # Maximum items to buffer
    overflow_policy: str = "drop_oldest"  # drop_oldest, drop_newest, block


@dataclass
class ResilienceMetrics:
    """Metrics for monitoring resilience behavior."""

    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    circuit_opens: int = 0
    circuit_closes: int = 0
    blocked_by_circuit: int = 0
    current_backoff_delay: float = 0.0
    buffer_size_bytes: int = 0
    buffer_items: int = 0
    dropped_items: int = 0
    last_success_time: float | None = None
    last_failure_time: float | None = None
    last_error: str | None = None


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by stopping requests to a failing service
    and allowing it time to recover.

    States:
        CLOSED: Normal operation, all requests go through
        OPEN: Service is failing, requests are blocked
        HALF_OPEN: Testing if service recovered, limited requests allowed
    """

    def __init__(self, config: CircuitBreakerConfig | None = None, name: str = "default"):
        self.config = config or CircuitBreakerConfig()
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0
        self._lock = threading.Lock()
        self._state_change_callbacks: list[Callable[[CircuitState, CircuitState], None]] = []

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for timeout transitions."""
        with self._lock:
            if self._state == CircuitState.OPEN and self._should_attempt_reset():
                self._transition_to(CircuitState.HALF_OPEN)
            return self._state

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try resetting the circuit."""
        if self._last_failure_time is None:
            return True
        return time.time() - self._last_failure_time >= self.config.reset_timeout

    def _transition_to(self, new_state: CircuitState):
        """Transition to a new state with logging and callbacks."""
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state
        logger.info(f"Circuit breaker '{self.name}' transitioned: {old_state.value} -> {new_state.value}")

        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0

        for callback in self._state_change_callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                logger.warning(f"Circuit breaker callback error: {e}")

    def should_allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        current_state = self.state  # This may trigger state transition

        with self._lock:
            if current_state == CircuitState.CLOSED:
                return True

            if current_state == CircuitState.OPEN:
                return False

            # HALF_OPEN: allow limited test requests
            if self._half_open_calls < self.config.half_open_max_calls:
                self._half_open_calls += 1
                return True

            return False

    def record_success(self):
        """Record a successful request."""
        with self._lock:
            self._failure_count = 0

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)

    def record_failure(self, _error: str | None = None):
        """Record a failed request."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._success_count = 0

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open immediately opens circuit
                self._transition_to(CircuitState.OPEN)
            elif self._failure_count >= self.config.failure_threshold:
                self._transition_to(CircuitState.OPEN)

    def reset(self):
        """Manually reset the circuit breaker to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None

    def on_state_change(self, callback: Callable[[CircuitState, CircuitState], None]):
        """Register a callback for state changes."""
        self._state_change_callbacks.append(callback)

    def get_stats(self) -> dict:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "last_failure_time": self._last_failure_time,
                "time_until_reset": max(
                    0,
                    self.config.reset_timeout - (time.time() - (self._last_failure_time or 0)),
                )
                if self._state == CircuitState.OPEN
                else 0,
            }


class ExponentialBackoff:
    """
    Exponential backoff with jitter.

    Increases delay between retry attempts exponentially,
    with optional random jitter to prevent thundering herd.
    """

    def __init__(self, config: BackoffConfig | None = None):
        self.config = config or BackoffConfig()
        self._attempt = 0
        self._lock = threading.Lock()

    @property
    def current_delay(self) -> float:
        """Get current delay without incrementing."""
        with self._lock:
            return self._calculate_delay(self._attempt)

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        import random

        base_delay = self.config.initial_delay * (self.config.multiplier**attempt)
        capped_delay = min(base_delay, self.config.max_delay)

        # Add jitter
        if self.config.jitter > 0:
            jitter_range = capped_delay * self.config.jitter
            capped_delay += random.uniform(-jitter_range, jitter_range)

        return max(0, capped_delay)

    def next_delay(self) -> float:
        """Get the next delay and increment attempt counter."""
        with self._lock:
            delay = self._calculate_delay(self._attempt)
            self._attempt += 1
            return delay

    def reset(self):
        """Reset backoff to initial state."""
        with self._lock:
            self._attempt = 0

    def get_stats(self) -> dict:
        """Get backoff statistics."""
        with self._lock:
            return {
                "attempt": self._attempt,
                "current_delay": self._calculate_delay(self._attempt),
                "max_delay": self.config.max_delay,
            }


@dataclass
class ResilientClientConfig:
    """Combined configuration for resilient client."""

    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    backoff: BackoffConfig = field(default_factory=BackoffConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    health_check_interval: float = 30.0  # Seconds between health checks
    health_check_timeout: float = 5.0  # Health check timeout


class ResilientClient:
    """
    Combines circuit breaker, backoff, and buffering for resilient operations.

    This is the main class for adding resilience to any HTTP client or
    network operation.

    Example:
        client = ResilientClient(name="log-shipper")

        def send_logs(logs):
            if not client.should_attempt():
                client.buffer_items(logs)
                return False

            try:
                response = http_post(endpoint, logs)
                client.record_success()
                return True
            except Exception as e:
                client.record_failure(str(e))
                client.buffer_items(logs)
                return False
    """

    def __init__(
        self,
        name: str = "default",
        config: ResilientClientConfig | None = None,
        on_circuit_open: Callable[[], None] | None = None,
        on_circuit_close: Callable[[], None] | None = None,
    ):
        self.name = name
        self.config = config or ResilientClientConfig()

        self._circuit = CircuitBreaker(self.config.circuit_breaker, name=name)
        self._backoff = ExponentialBackoff(self.config.backoff)
        self._metrics = ResilienceMetrics()
        self._lock = threading.Lock()

        # Register callbacks
        if on_circuit_open or on_circuit_close:

            def state_callback(_old: CircuitState, new: CircuitState):
                if new == CircuitState.OPEN and on_circuit_open:
                    on_circuit_open()
                elif new == CircuitState.CLOSED and on_circuit_close:
                    on_circuit_close()

            self._circuit.on_state_change(state_callback)

        # Track circuit state changes for metrics
        def metrics_callback(_old: CircuitState, new: CircuitState):
            with self._lock:
                if new == CircuitState.OPEN:
                    self._metrics.circuit_opens += 1
                elif new == CircuitState.CLOSED:
                    self._metrics.circuit_closes += 1

        self._circuit.on_state_change(metrics_callback)

    @property
    def circuit_state(self) -> CircuitState:
        """Get current circuit breaker state."""
        return self._circuit.state

    @property
    def is_healthy(self) -> bool:
        """Check if client is in healthy state (circuit closed)."""
        return self._circuit.state == CircuitState.CLOSED

    def should_attempt(self) -> bool:
        """
        Check if an operation should be attempted.

        Returns False if circuit is open, True otherwise.
        Updates metrics accordingly.
        """
        allowed = self._circuit.should_allow_request()

        with self._lock:
            self._metrics.total_attempts += 1
            if not allowed:
                self._metrics.blocked_by_circuit += 1

        return allowed

    def record_success(self):
        """Record a successful operation."""
        self._circuit.record_success()
        self._backoff.reset()

        with self._lock:
            self._metrics.successful_attempts += 1
            self._metrics.last_success_time = time.time()
            self._metrics.current_backoff_delay = 0

    def record_failure(self, error: str | None = None):
        """Record a failed operation."""
        self._circuit.record_failure(error)

        with self._lock:
            self._metrics.failed_attempts += 1
            self._metrics.last_failure_time = time.time()
            self._metrics.last_error = error
            self._metrics.current_backoff_delay = self._backoff.current_delay

    def get_backoff_delay(self) -> float:
        """Get current backoff delay (call after recording failure)."""
        return self._backoff.next_delay()

    def reset(self):
        """Reset all resilience state."""
        self._circuit.reset()
        self._backoff.reset()
        with self._lock:
            self._metrics = ResilienceMetrics()

    def get_metrics(self) -> dict:
        """Get combined metrics from all components."""
        with self._lock:
            metrics = {
                "name": self.name,
                "circuit": self._circuit.get_stats(),
                "backoff": self._backoff.get_stats(),
                "totals": {
                    "attempts": self._metrics.total_attempts,
                    "successes": self._metrics.successful_attempts,
                    "failures": self._metrics.failed_attempts,
                    "blocked_by_circuit": self._metrics.blocked_by_circuit,
                    "circuit_opens": self._metrics.circuit_opens,
                    "circuit_closes": self._metrics.circuit_closes,
                },
                "timing": {
                    "last_success": self._metrics.last_success_time,
                    "last_failure": self._metrics.last_failure_time,
                    "current_backoff": self._metrics.current_backoff_delay,
                },
                "last_error": self._metrics.last_error,
            }
            return metrics

    def format_status(self) -> str:
        """Get human-readable status string."""
        metrics = self.get_metrics()
        circuit = metrics["circuit"]
        totals = metrics["totals"]

        status_parts = [
            f"circuit={circuit['state']}",
            f"success_rate={totals['successes']}/{totals['attempts']}",
        ]

        if circuit["state"] == "open":
            status_parts.append(f"reset_in={circuit['time_until_reset']:.0f}s")

        if metrics["last_error"]:
            error_preview = metrics["last_error"][:50]
            status_parts.append(f"last_error='{error_preview}'")

        return f"ResilientClient[{self.name}]: " + ", ".join(status_parts)


def with_resilience(
    func: Callable,
    client: ResilientClient,
    on_blocked: Callable | None = None,
) -> Callable:
    """
    Decorator to add resilience to a function.

    Example:
        client = ResilientClient(name="api-client")

        @with_resilience(client)
        def call_api():
            return requests.post(url, data)

        # Now call_api() has circuit breaker and backoff
        result = call_api()
    """

    def wrapper(*args, **kwargs):
        if not client.should_attempt():
            if on_blocked:
                return on_blocked(*args, **kwargs)
            return None

        try:
            result = func(*args, **kwargs)
            client.record_success()
            return result
        except Exception as e:
            client.record_failure(str(e))
            raise

    return wrapper
