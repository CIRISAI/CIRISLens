"""Pytest configuration and shared fixtures for CIRISLens tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def app() -> FastAPI:
    """Create a test FastAPI application instance."""
    # Import here to avoid import-time side effects
    from api.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_db_pool(mocker):
    """Mock the database connection pool."""
    pool = mocker.MagicMock()
    pool.acquire = mocker.AsyncMock()
    pool.release = mocker.AsyncMock()
    return pool


@pytest.fixture
def sample_log_entry() -> dict:
    """Return a sample log entry for testing."""
    return {
        "timestamp": "2024-01-15T10:30:00Z",
        "service_name": "test-service",
        "level": "INFO",
        "message": "Test log message",
        "event": "test_event",
        "extra": {"key": "value"},
    }


@pytest.fixture
def sample_log_batch(sample_log_entry: dict) -> list[dict]:
    """Return a batch of sample log entries."""
    return [
        sample_log_entry,
        {
            **sample_log_entry,
            "level": "ERROR",
            "message": "Test error message",
            "event": "error_event",
        },
        {
            **sample_log_entry,
            "level": "WARNING",
            "message": "Test warning message",
            "event": "warning_event",
        },
    ]
