"""
Service Log Ingestion for CIRISLens

Handles log ingestion from CIRISBilling, CIRISProxy, and CIRISManager.
"""

import hashlib
import json
import re
import logging
from datetime import datetime
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# PII redaction patterns
REDACT_PATTERNS = [
    (re.compile(r'Bearer [A-Za-z0-9\-_\.]+'), 'Bearer [REDACTED]'),
    (re.compile(r'token=[A-Za-z0-9\-_\.]+'), 'token=[REDACTED]'),
    (re.compile(r'password=\S+'), 'password=[REDACTED]'),
    (re.compile(r'secret=\S+'), 'secret=[REDACTED]'),
    (re.compile(r'api_key=\S+'), 'api_key=[REDACTED]'),
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL]'),
    (re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'), '[CARD]'),
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN]'),
]


def sanitize_message(message: Optional[str]) -> Optional[str]:
    """Remove PII and secrets from log messages."""
    if not message:
        return message

    for pattern, replacement in REDACT_PATTERNS:
        message = pattern.sub(replacement, message)

    return message


def hash_user_id(user_id: str) -> str:
    """Hash user identifier for privacy."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:16]


class LogIngestService:
    """Service for ingesting logs from external services."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._token_cache: dict[str, str] = {}  # service_name -> token_hash
        self._cache_loaded = False

    async def _load_token_cache(self):
        """Load service tokens into memory cache."""
        if self._cache_loaded:
            return

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT service_name, token_hash
                FROM cirislens.service_tokens
                WHERE enabled = TRUE
            """)

            self._token_cache = {row['service_name']: row['token_hash'] for row in rows}
            self._cache_loaded = True
            logger.info(f"Loaded {len(self._token_cache)} service tokens")

    async def reload_tokens(self):
        """Force reload of token cache."""
        self._cache_loaded = False
        await self._load_token_cache()

    async def verify_token(self, token: str) -> Optional[str]:
        """
        Verify a service token and return the service name if valid.
        Returns None if invalid.
        """
        await self._load_token_cache()

        token_hash = hashlib.sha256(token.encode()).hexdigest()

        for service_name, stored_hash in self._token_cache.items():
            if stored_hash == token_hash:
                # Update last_used_at
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE cirislens.service_tokens
                        SET last_used_at = NOW()
                        WHERE service_name = $1
                    """, service_name)
                return service_name

        return None

    async def create_token(self, service_name: str, created_by: str, description: str = None) -> str:
        """
        Create a new service token.
        Returns the raw token (only shown once).
        """
        import secrets

        # Generate a secure token
        raw_token = f"svc_{secrets.token_urlsafe(32)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO cirislens.service_tokens (service_name, token_hash, description, created_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (service_name) DO UPDATE SET
                    token_hash = EXCLUDED.token_hash,
                    description = EXCLUDED.description,
                    created_by = EXCLUDED.created_by,
                    created_at = NOW(),
                    last_used_at = NULL
            """, service_name, token_hash, description, created_by)

        # Reload cache
        await self.reload_tokens()

        return raw_token

    async def revoke_token(self, service_name: str) -> bool:
        """Revoke a service token."""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE cirislens.service_tokens
                SET enabled = FALSE
                WHERE service_name = $1
            """, service_name)

        # Reload cache
        await self.reload_tokens()

        return result == "UPDATE 1"

    async def get_tokens(self) -> list[dict]:
        """Get all service tokens (without actual token values)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT service_name, description, created_at, created_by,
                       last_used_at, enabled
                FROM cirislens.service_tokens
                ORDER BY service_name
            """)

            return [
                {
                    "service_name": row["service_name"],
                    "description": row["description"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "created_by": row["created_by"],
                    "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
                    "enabled": row["enabled"],
                }
                for row in rows
            ]

    async def ingest_logs(self, service_name: str, logs: list[dict]) -> dict:
        """
        Ingest a batch of logs from a service.

        Returns: {"accepted": int, "rejected": int, "errors": list}
        """
        accepted = 0
        rejected = 0
        errors = []

        async with self.pool.acquire() as conn:
            for i, log in enumerate(logs):
                try:
                    # Parse and validate
                    timestamp = log.get("timestamp")
                    if isinstance(timestamp, str):
                        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    elif not timestamp:
                        timestamp = datetime.utcnow()

                    level = log.get("level", "INFO").upper()
                    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                        level = "INFO"

                    # Sanitize message
                    message = sanitize_message(log.get("message"))

                    # Hash user_id if present
                    user_hash = None
                    if "user_id" in log.get("attributes", {}):
                        user_hash = hash_user_id(str(log["attributes"].pop("user_id")))
                    elif "user_hash" in log:
                        user_hash = log.get("user_hash")

                    # Insert log
                    await conn.execute("""
                        INSERT INTO cirislens.service_logs
                        (service_name, server_id, timestamp, level, event, logger,
                         message, request_id, trace_id, user_hash, attributes)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                        service_name,
                        log.get("server_id"),
                        timestamp,
                        level,
                        log.get("event"),
                        log.get("logger"),
                        message,
                        log.get("request_id"),
                        log.get("trace_id"),
                        user_hash,
                        json.dumps(log.get("attributes", {})),
                    )

                    accepted += 1

                except Exception as e:
                    rejected += 1
                    if len(errors) < 10:  # Limit error messages
                        errors.append(f"Log {i}: {str(e)}")
                    logger.warning(f"Failed to ingest log {i}: {e}")

        logger.info(f"Ingested {accepted} logs from {service_name}, rejected {rejected}")

        return {
            "accepted": accepted,
            "rejected": rejected,
            "errors": errors,
        }
