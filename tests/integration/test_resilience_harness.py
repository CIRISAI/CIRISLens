"""
Integration test harness for resilience patterns.

Simulates endpoint failures and verifies circuit breaker, backoff,
and buffer behavior work correctly end-to-end.
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from sdk.logshipper import LogShipper
from sdk.resilience import (
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ExponentialBackoff,
    ResilientClient,
    ResilientClientConfig,
)


class MockEndpointHandler(BaseHTTPRequestHandler):
    """HTTP handler that can simulate various failure modes."""

    # Class-level state for controlling behavior
    failure_mode = None  # None, "timeout", "503", "connection_refused"
    request_count = 0
    received_logs = []
    lock = threading.Lock()

    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass

    def do_POST(self):
        with self.lock:
            MockEndpointHandler.request_count += 1

        if self.failure_mode == "timeout":
            time.sleep(30)  # Long timeout
            return

        if self.failure_mode == "503":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Service Unavailable")
            return

        if self.failure_mode == "401":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        # Success path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        with self.lock:
            for line in body.decode().strip().split("\n"):
                if line:
                    MockEndpointHandler.received_logs.append(json.loads(line))

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    @classmethod
    def reset(cls):
        """Reset all state."""
        cls.failure_mode = None
        cls.request_count = 0
        cls.received_logs = []


@pytest.fixture
def mock_server():
    """Start a mock HTTP server for testing."""
    MockEndpointHandler.reset()

    server = HTTPServer(("127.0.0.1", 0), MockEndpointHandler)
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://127.0.0.1:{port}/ingest", MockEndpointHandler

    server.shutdown()


class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker with real timing."""

    def test_circuit_opens_and_recovers(self, mock_server):
        """Test full circuit breaker lifecycle with mock endpoint."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="test",
            token="test_token",
            endpoint=endpoint,
            circuit_failure_threshold=3,
            circuit_reset_timeout=0.5,  # Fast reset for testing
            flush_interval=100,  # Disable auto-flush
            max_retries=1,  # Fast failure
            timeout=0.5,
        )

        # Phase 1: Endpoint is down
        handler.failure_mode = "503"

        # Send logs that will fail
        for i in range(5):
            shipper.info(f"Test message {i}")

        # Trigger flushes to hit failure threshold
        for _ in range(4):
            shipper.flush()
            time.sleep(0.1)

        # Circuit should be open
        assert shipper.circuit_state == "open"
        stats = shipper.get_stats()
        assert stats["error_count"] >= 3

        # Phase 2: Wait for reset timeout
        time.sleep(0.6)

        # Phase 3: Endpoint recovers
        handler.failure_mode = None

        # Next flush should succeed (half-open -> closed)
        shipper.info("Recovery message")
        result = shipper.flush()

        # Give it a moment to process
        time.sleep(0.2)

        # Circuit should be closed again
        assert shipper.circuit_state == "closed"
        assert shipper.is_healthy

        shipper.shutdown()

    def test_blocked_requests_are_counted(self, mock_server):
        """Test that blocked requests during open circuit are tracked."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="test",
            token="test_token",
            endpoint=endpoint,
            circuit_failure_threshold=2,
            circuit_reset_timeout=10.0,  # Long reset
            flush_interval=100,
            max_retries=1,
            timeout=0.5,
        )

        handler.failure_mode = "503"

        # Trigger circuit open
        for _ in range(3):
            shipper.info("fail")
            shipper.flush()
            time.sleep(0.1)

        assert shipper.circuit_state == "open"

        # These flushes should be blocked
        initial_request_count = handler.request_count
        for _ in range(5):
            shipper.flush()

        # No new requests should have been made
        assert handler.request_count == initial_request_count

        stats = shipper.get_stats()
        assert stats.get("blocked_by_circuit", 0) >= 1

        shipper.shutdown()


class TestBackoffIntegration:
    """Integration tests for exponential backoff."""

    def test_backoff_delays_increase(self):
        """Verify backoff delays grow exponentially."""
        config = BackoffConfig(initial_delay=0.1, multiplier=2.0, max_delay=1.0, jitter=0)
        backoff = ExponentialBackoff(config=config)

        delays = [backoff.next_delay() for _ in range(5)]

        assert delays[0] == pytest.approx(0.1, abs=0.01)
        assert delays[1] == pytest.approx(0.2, abs=0.01)
        assert delays[2] == pytest.approx(0.4, abs=0.01)
        assert delays[3] == pytest.approx(0.8, abs=0.01)
        assert delays[4] == pytest.approx(1.0, abs=0.01)  # Capped

    def test_backoff_with_jitter(self):
        """Verify jitter adds randomness to delays."""
        config = BackoffConfig(initial_delay=1.0, jitter=0.5)
        backoff = ExponentialBackoff(config=config)

        delays = [backoff.next_delay() for _ in range(10)]

        # With 50% jitter, delays should vary
        assert len(set(delays)) > 1  # Not all the same


class TestBufferLimits:
    """Tests for buffer size limiting."""

    def test_drops_logs_when_buffer_full(self, mock_server):
        """Logs are dropped when buffer exceeds limits."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="test",
            token="test_token",
            endpoint=endpoint,
            max_buffer_items=10,
            max_buffer_bytes=10 * 1024 * 1024,
            flush_interval=100,
            circuit_failure_threshold=100,  # High threshold
        )

        handler.failure_mode = "503"

        # Add more logs than buffer allows
        for i in range(20):
            shipper.info(f"Message {i}")

        stats = shipper.get_stats()
        assert stats["buffer_size"] <= 10
        assert stats["dropped_count"] >= 10

        shipper.shutdown()

    def test_buffer_bytes_limit(self, mock_server):
        """Logs are dropped when buffer bytes exceeded."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="test",
            token="test_token",
            endpoint=endpoint,
            max_buffer_items=1000,
            max_buffer_bytes=500,  # Very small limit
            flush_interval=100,
            circuit_failure_threshold=100,
        )

        handler.failure_mode = "503"

        # Add logs until bytes limit hit
        for i in range(50):
            shipper.info(f"This is a longer message to fill up the buffer quickly {i}")

        stats = shipper.get_stats()
        assert stats["dropped_count"] > 0
        assert stats["buffer_bytes"] <= 500

        shipper.shutdown()


class TestCallbacks:
    """Tests for circuit state change callbacks."""

    def test_callbacks_invoked_on_state_change(self, mock_server):
        """Callbacks fire when circuit opens and closes."""
        endpoint, handler = mock_server

        on_open = MagicMock()
        on_close = MagicMock()

        shipper = LogShipper(
            service_name="test",
            token="test_token",
            endpoint=endpoint,
            circuit_failure_threshold=2,
            circuit_reset_timeout=0.3,
            flush_interval=100,
            max_retries=1,
            timeout=0.5,
            on_circuit_open=on_open,
            on_circuit_close=on_close,
        )

        handler.failure_mode = "503"

        # Trigger circuit open
        for _ in range(3):
            shipper.info("fail")
            shipper.flush()
            time.sleep(0.1)

        assert on_open.called

        # Wait for reset and recover
        time.sleep(0.4)
        handler.failure_mode = None

        shipper.info("recover")
        shipper.flush()
        time.sleep(0.2)

        assert on_close.called

        shipper.shutdown()


class TestResilientClientStandalone:
    """Tests for using ResilientClient independently."""

    def test_full_lifecycle(self):
        """Test ResilientClient through full open-close cycle."""
        config = ResilientClientConfig(
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=2,
                reset_timeout=0.2,
            ),
            backoff=BackoffConfig(initial_delay=0.1, jitter=0),
        )

        client = ResilientClient(name="test", config=config)

        # Initially healthy
        assert client.is_healthy
        assert client.should_attempt()

        # Fail until circuit opens
        client.record_failure("error 1")
        assert client.is_healthy

        client.should_attempt()
        client.record_failure("error 2")
        assert not client.is_healthy
        assert client.circuit_state == CircuitState.OPEN

        # Blocked while open
        assert not client.should_attempt()

        # Wait for reset
        time.sleep(0.3)

        # Should allow test request (half-open)
        assert client.should_attempt()

        # Success closes circuit
        client.record_success()
        assert client.is_healthy
        assert client.circuit_state == CircuitState.CLOSED

    def test_metrics_accuracy(self):
        """Verify metrics are tracked correctly."""
        client = ResilientClient(name="metrics-test")

        for _ in range(5):
            client.should_attempt()
            client.record_success()

        for _ in range(3):
            client.should_attempt()
            client.record_failure("error")

        metrics = client.get_metrics()
        assert metrics["totals"]["attempts"] == 8
        assert metrics["totals"]["successes"] == 5
        assert metrics["totals"]["failures"] == 3


class TestEndToEnd:
    """End-to-end tests simulating real usage patterns."""

    def test_logs_delivered_after_recovery(self, mock_server):
        """Logs buffered during outage are delivered after recovery."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="e2e-test",
            token="test_token",
            endpoint=endpoint,
            circuit_failure_threshold=2,
            circuit_reset_timeout=0.3,
            flush_interval=100,
            max_retries=1,
            timeout=0.5,
            batch_size=100,
        )

        # Phase 1: Endpoint down
        handler.failure_mode = "503"

        for i in range(5):
            shipper.info(f"Outage message {i}", event="outage_test")
            shipper.flush()
            time.sleep(0.05)

        assert shipper.circuit_state == "open"

        # Phase 2: Endpoint recovers
        time.sleep(0.4)
        handler.failure_mode = None

        # Phase 3: Flush remaining logs
        shipper.info("Recovery message", event="recovery")
        shipper.flush()
        time.sleep(0.2)

        # Verify logs were received
        assert len(handler.received_logs) >= 1
        messages = [log["message"] for log in handler.received_logs]
        assert any("Recovery" in msg for msg in messages)

        shipper.shutdown()

    def test_high_volume_with_failures(self, mock_server):
        """Handle high log volume with intermittent failures."""
        endpoint, handler = mock_server

        shipper = LogShipper(
            service_name="volume-test",
            token="test_token",
            endpoint=endpoint,
            circuit_failure_threshold=5,
            circuit_reset_timeout=0.2,
            flush_interval=0.1,
            max_retries=1,
            timeout=0.5,
            batch_size=50,
        )

        # Generate logs with intermittent failures
        for i in range(100):
            if i % 20 == 10:
                handler.failure_mode = "503"
            elif i % 20 == 15:
                handler.failure_mode = None

            shipper.info(f"Volume message {i}")
            time.sleep(0.01)

        # Final flush
        handler.failure_mode = None
        time.sleep(0.5)
        shipper.flush()
        time.sleep(0.2)

        # Should have received most logs
        stats = shipper.get_stats()
        total_processed = stats["sent_count"] + stats["buffer_size"] + stats["dropped_count"]
        assert total_processed == 100

        shipper.shutdown()
