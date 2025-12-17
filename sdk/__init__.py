"""
CIRISLens SDK - Client libraries for sending telemetry to CIRISLens.

This package provides:
- LogShipper: Batched log shipping with circuit breaker and backoff
- resilience: Reusable circuit breaker and exponential backoff patterns

Usage:
    from sdk import LogShipper, setup_logging
    from sdk.resilience import ResilientClient, CircuitBreaker

Example:
    # Simple log shipping
    from sdk import setup_logging

    shipper = setup_logging(
        service_name="my-service",
        token="svc_xxx",
    )

    import logging
    logger = logging.getLogger(__name__)
    logger.info("Service started")
"""

from .logshipper import (
    DEFAULT_ENDPOINT,
    LogShipper,
    LogShipperHandler,
    from_env,
    setup_logging,
)
from .resilience import (
    BackoffConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ExponentialBackoff,
    ResilientClient,
    ResilientClientConfig,
)

__all__ = [
    # LogShipper
    "LogShipper",
    "LogShipperHandler",
    "setup_logging",
    "from_env",
    "DEFAULT_ENDPOINT",
    # Resilience
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "ExponentialBackoff",
    "BackoffConfig",
    "ResilientClient",
    "ResilientClientConfig",
]

__version__ = "1.1.0"
