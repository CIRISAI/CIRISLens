"""Tests for the resilience module (circuit breaker, backoff, etc.)."""

import time
from unittest.mock import MagicMock

import pytest

from sdk.resilience import (
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ExponentialBackoff,
    ResilientClient,
    ResilientClientConfig,
)


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_initial_state_is_closed(self):
        """Circuit breaker starts in closed state."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_allows_requests_when_closed(self):
        """Requests are allowed when circuit is closed."""
        cb = CircuitBreaker()
        assert cb.should_allow_request() is True

    def test_opens_after_failure_threshold(self):
        """Circuit opens after reaching failure threshold."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(config=config)

        # Record failures up to threshold
        cb.record_failure("error 1")
        assert cb.state == CircuitState.CLOSED
        cb.record_failure("error 2")
        assert cb.state == CircuitState.CLOSED
        cb.record_failure("error 3")
        assert cb.state == CircuitState.OPEN

    def test_blocks_requests_when_open(self):
        """Requests are blocked when circuit is open."""
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout=300)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN
        assert cb.should_allow_request() is False

    def test_success_resets_failure_count(self):
        """Success resets the failure counter."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error 1")
        cb.record_failure("error 2")
        cb.record_success()

        # Failure count should be reset, need 3 more failures
        cb.record_failure("error 3")
        cb.record_failure("error 4")
        assert cb.state == CircuitState.CLOSED

    def test_transitions_to_half_open_after_timeout(self):
        """Circuit transitions to half-open after reset timeout."""
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout=0.1)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)
        # Accessing state triggers the check
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_limited_requests(self):
        """Half-open state allows limited test requests."""
        config = CircuitBreakerConfig(
            failure_threshold=1, reset_timeout=0.1, half_open_max_calls=2
        )
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        time.sleep(0.15)

        # First two requests allowed
        assert cb.should_allow_request() is True
        assert cb.should_allow_request() is True
        # Third request blocked
        assert cb.should_allow_request() is False

    def test_half_open_closes_on_success(self):
        """Circuit closes after success in half-open state."""
        config = CircuitBreakerConfig(
            failure_threshold=1, reset_timeout=0.1, success_threshold=1
        )
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        time.sleep(0.15)
        cb.should_allow_request()  # Transition to half-open

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_opens_on_failure(self):
        """Circuit opens again on failure in half-open state."""
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout=0.1)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        time.sleep(0.15)
        cb.should_allow_request()  # Transition to half-open

        cb.record_failure("another error")
        assert cb.state == CircuitState.OPEN

    def test_reset_clears_state(self):
        """Manual reset clears all state."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.should_allow_request() is True

    def test_state_change_callbacks(self):
        """Callbacks are invoked on state changes."""
        callback = MagicMock()
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout=0.1)
        cb = CircuitBreaker(config=config)
        cb.on_state_change(callback)

        cb.record_failure("error")
        callback.assert_called_once_with(CircuitState.CLOSED, CircuitState.OPEN)

        callback.reset_mock()
        time.sleep(0.15)
        cb.should_allow_request()  # Triggers half-open transition
        callback.assert_called_once_with(CircuitState.OPEN, CircuitState.HALF_OPEN)

    def test_get_stats(self):
        """Stats include current state and counters."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(config=config)

        cb.record_failure("error 1")
        cb.record_failure("error 2")

        stats = cb.get_stats()
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 2


class TestExponentialBackoff:
    """Tests for ExponentialBackoff class."""

    def test_initial_delay(self):
        """First delay matches initial_delay config."""
        config = BackoffConfig(initial_delay=1.0)
        backoff = ExponentialBackoff(config=config)

        delay = backoff.next_delay()
        # Allow for jitter
        assert 0.8 <= delay <= 1.2

    def test_exponential_growth(self):
        """Delay grows exponentially."""
        config = BackoffConfig(initial_delay=1.0, multiplier=2.0, jitter=0)
        backoff = ExponentialBackoff(config=config)

        assert backoff.next_delay() == 1.0
        assert backoff.next_delay() == 2.0
        assert backoff.next_delay() == 4.0
        assert backoff.next_delay() == 8.0

    def test_max_delay_cap(self):
        """Delay is capped at max_delay."""
        config = BackoffConfig(initial_delay=100.0, max_delay=150.0, jitter=0)
        backoff = ExponentialBackoff(config=config)

        assert backoff.next_delay() == 100.0
        assert backoff.next_delay() == 150.0  # Capped
        assert backoff.next_delay() == 150.0  # Still capped

    def test_reset(self):
        """Reset returns to initial delay."""
        config = BackoffConfig(initial_delay=1.0, jitter=0)
        backoff = ExponentialBackoff(config=config)

        backoff.next_delay()
        backoff.next_delay()
        backoff.reset()

        assert backoff.next_delay() == 1.0

    def test_current_delay_without_increment(self):
        """current_delay property doesn't increment counter."""
        config = BackoffConfig(initial_delay=1.0, jitter=0)
        backoff = ExponentialBackoff(config=config)

        assert backoff.current_delay == 1.0
        assert backoff.current_delay == 1.0
        assert backoff.next_delay() == 1.0  # First call
        assert backoff.current_delay == 2.0  # Now it's incremented


class TestResilientClient:
    """Tests for ResilientClient class."""

    def test_initial_state_is_healthy(self):
        """Client starts in healthy state."""
        client = ResilientClient(name="test")
        assert client.is_healthy is True
        assert client.circuit_state == CircuitState.CLOSED

    def test_should_attempt_when_healthy(self):
        """Attempts are allowed when client is healthy."""
        client = ResilientClient(name="test")
        assert client.should_attempt() is True

    def test_records_success(self):
        """Success is recorded correctly."""
        client = ResilientClient(name="test")
        client.should_attempt()
        client.record_success()

        metrics = client.get_metrics()
        assert metrics["totals"]["successes"] == 1

    def test_records_failure(self):
        """Failure is recorded correctly."""
        client = ResilientClient(name="test")
        client.should_attempt()
        client.record_failure("test error")

        metrics = client.get_metrics()
        assert metrics["totals"]["failures"] == 1
        assert metrics["last_error"] == "test error"

    def test_circuit_opens_after_failures(self):
        """Circuit opens after threshold failures."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2)
        )
        client = ResilientClient(name="test", config=config)

        client.should_attempt()
        client.record_failure("error 1")
        assert client.is_healthy is True

        client.should_attempt()
        client.record_failure("error 2")
        assert client.is_healthy is False
        assert client.circuit_state == CircuitState.OPEN

    def test_blocks_attempts_when_circuit_open(self):
        """Attempts are blocked when circuit is open."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1)
        )
        client = ResilientClient(name="test", config=config)

        client.should_attempt()
        client.record_failure("error")

        assert client.should_attempt() is False

        metrics = client.get_metrics()
        assert metrics["totals"]["blocked_by_circuit"] == 1

    def test_backoff_increases_on_failure(self):
        """Backoff delay increases after each failure."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=10),
            backoff=BackoffConfig(initial_delay=1.0, jitter=0),
        )
        client = ResilientClient(name="test", config=config)

        client.should_attempt()
        client.record_failure("error 1")
        delay1 = client.get_backoff_delay()

        client.should_attempt()
        client.record_failure("error 2")
        delay2 = client.get_backoff_delay()

        assert delay2 > delay1

    def test_success_resets_backoff(self):
        """Success resets backoff to initial delay."""
        config = ResilientClientConfig(
            backoff=BackoffConfig(initial_delay=1.0, jitter=0)
        )
        client = ResilientClient(name="test", config=config)

        client.should_attempt()
        client.record_failure("error")
        delay1 = client.get_backoff_delay()
        assert delay1 == 1.0  # First failure, initial delay

        client.should_attempt()
        client.record_success()

        # Next failure should start from initial delay again
        client.should_attempt()
        client.record_failure("error")
        delay2 = client.get_backoff_delay()
        assert delay2 == 1.0  # Reset to initial delay

    def test_callbacks_on_circuit_state_change(self):
        """Callbacks are invoked when circuit state changes."""
        on_open = MagicMock()
        on_close = MagicMock()

        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=1, reset_timeout=0.1, success_threshold=1
            )
        )
        client = ResilientClient(
            name="test",
            config=config,
            on_circuit_open=on_open,
            on_circuit_close=on_close,
        )

        client.should_attempt()
        client.record_failure("error")
        on_open.assert_called_once()

        time.sleep(0.15)
        client.should_attempt()  # Half-open
        client.record_success()
        on_close.assert_called_once()

    def test_format_status(self):
        """Status string is human-readable."""
        client = ResilientClient(name="test-service")
        status = client.format_status()

        assert "test-service" in status
        assert "circuit=" in status
        assert "success_rate=" in status

    def test_reset_clears_all_state(self):
        """Reset clears circuit and backoff state."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1)
        )
        client = ResilientClient(name="test", config=config)

        client.should_attempt()
        client.record_failure("error")
        assert client.circuit_state == CircuitState.OPEN

        client.reset()
        assert client.circuit_state == CircuitState.CLOSED
        assert client.is_healthy is True

    def test_metrics_track_circuit_opens(self):
        """Metrics track how many times circuit has opened."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=1, reset_timeout=0.1, success_threshold=1
            )
        )
        client = ResilientClient(name="test", config=config)

        # Open circuit
        client.should_attempt()
        client.record_failure("error 1")

        # Recover
        time.sleep(0.15)
        client.should_attempt()
        client.record_success()

        # Open again
        client.should_attempt()
        client.record_failure("error 2")

        metrics = client.get_metrics()
        assert metrics["totals"]["circuit_opens"] == 2
        assert metrics["totals"]["circuit_closes"] == 1
