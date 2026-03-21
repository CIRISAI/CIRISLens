"""
Tests for DSAR (Data Subject Access Request) trace deletion endpoint.

Tests cover:
- DSARDeleteRequest model validation
- Signature verification for DSAR requests
- Endpoint behavior: successful deletion, no traces found, signature failure
- Audit trail recording
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nacl.signing import SigningKey

from api.accord_api import (
    DSARDeleteRequest,
    _verify_dsar_signature,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def signing_keypair():
    """Generate an Ed25519 signing keypair for tests."""
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return signing_key, verify_key


@pytest.fixture
def key_id():
    return "test-key-2026-01"


@pytest.fixture
def public_keys(signing_keypair, key_id):
    """Public keys dict matching the format used by load_public_keys."""
    _, verify_key = signing_keypair
    return {key_id: bytes(verify_key)}


@pytest.fixture
def make_signed_request(signing_keypair, key_id):
    """Factory to create properly signed DSAR requests."""

    def _make(
        agent_id_hash="abc123def456",
        request_type="delete_all_traces",
        requested_at="2026-03-20T12:00:00+00:00",
    ):
        signing_key, _ = signing_keypair
        # Canonical payload that the agent signs
        signed_payload = {
            "agent_id_hash": agent_id_hash,
            "request_type": request_type,
            "requested_at": requested_at,
        }
        message = json.dumps(
            signed_payload, sort_keys=True, separators=(",", ":")
        ).encode()
        signed = signing_key.sign(message)
        signature = base64.b64encode(signed.signature).decode()

        return DSARDeleteRequest(
            agent_id_hash=agent_id_hash,
            request_type=request_type,
            requested_at=requested_at,
            signature=signature,
            signature_key_id=key_id,
        )

    return _make


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestDSARDeleteRequest:
    """Tests for the DSARDeleteRequest Pydantic model."""

    def test_valid_request(self, make_signed_request):
        req = make_signed_request()
        assert req.agent_id_hash == "abc123def456"
        assert req.request_type == "delete_all_traces"
        assert req.reason == "User DSAR self-service request"

    def test_agent_id_hash_too_short(self):
        with pytest.raises(ValueError):
            DSARDeleteRequest(
                agent_id_hash="abc",  # too short (min 8)
                requested_at="2026-03-20T12:00:00+00:00",
                signature="sig",
                signature_key_id="key",
            )

    def test_agent_id_hash_too_long(self):
        with pytest.raises(ValueError):
            DSARDeleteRequest(
                agent_id_hash="a" * 65,  # too long (max 64)
                requested_at="2026-03-20T12:00:00+00:00",
                signature="sig",
                signature_key_id="key",
            )

    def test_invalid_request_type(self):
        with pytest.raises(ValueError):
            DSARDeleteRequest(
                agent_id_hash="abc123def456",
                request_type="delete_some_traces",
                requested_at="2026-03-20T12:00:00+00:00",
                signature="sig",
                signature_key_id="key",
            )

    def test_custom_reason(self):
        req = DSARDeleteRequest(
            agent_id_hash="abc123def456",
            requested_at="2026-03-20T12:00:00+00:00",
            signature="sig",
            signature_key_id="key",
            reason="Custom reason for deletion",
        )
        assert req.reason == "Custom reason for deletion"


# =============================================================================
# Signature Verification Tests
# =============================================================================


class TestDSARSignatureVerification:
    """Tests for _verify_dsar_signature."""

    def test_valid_signature(self, make_signed_request, public_keys):
        req = make_signed_request()
        is_valid, error = _verify_dsar_signature(req, public_keys)
        assert is_valid is True
        assert error is None

    def test_unknown_key_id(self, make_signed_request):
        req = make_signed_request()
        is_valid, error = _verify_dsar_signature(req, {})
        assert is_valid is False
        assert "Unknown signer key" in error

    def test_wrong_signature(self, public_keys, key_id):
        """Signature from a different key should fail."""
        other_key = SigningKey.generate()
        payload = {
            "agent_id_hash": "abc123def456",
            "request_type": "delete_all_traces",
            "requested_at": "2026-03-20T12:00:00+00:00",
        }
        message = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()
        signed = other_key.sign(message)
        signature = base64.b64encode(signed.signature).decode()

        req = DSARDeleteRequest(
            agent_id_hash="abc123def456",
            requested_at="2026-03-20T12:00:00+00:00",
            signature=signature,
            signature_key_id=key_id,
        )
        is_valid, error = _verify_dsar_signature(req, public_keys)
        assert is_valid is False
        assert "Invalid signature" in error

    def test_tampered_payload(self, make_signed_request, public_keys):
        """If the agent_id_hash is changed after signing, verification fails."""
        req = make_signed_request(agent_id_hash="abc123def456")
        # Tamper with the hash
        req.agent_id_hash = "tampered_hash_value"
        is_valid, error = _verify_dsar_signature(req, public_keys)
        assert is_valid is False

    def test_urlsafe_base64_signature(self, signing_keypair, public_keys, key_id):
        """URL-safe base64 signatures should also verify."""
        signing_key, _ = signing_keypair
        payload = {
            "agent_id_hash": "abc123def456",
            "request_type": "delete_all_traces",
            "requested_at": "2026-03-20T12:00:00+00:00",
        }
        message = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()
        signed = signing_key.sign(message)
        # Use URL-safe base64
        signature = base64.urlsafe_b64encode(signed.signature).decode()

        req = DSARDeleteRequest(
            agent_id_hash="abc123def456",
            requested_at="2026-03-20T12:00:00+00:00",
            signature=signature,
            signature_key_id=key_id,
        )
        is_valid, error = _verify_dsar_signature(req, public_keys)
        assert is_valid is True


# =============================================================================
# Endpoint Tests (with mocked database)
# =============================================================================


def _setup_mock_pool(mock_conn):
    """Create a mock database pool with async context manager support."""
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_pool


@pytest.fixture(autouse=True)
def _reset_public_keys_cache():
    """Reset the public keys cache before each test."""
    import api.accord_api as accord_module
    accord_module._public_keys_cache = {}
    accord_module._public_keys_loaded = False
    yield
    accord_module._public_keys_cache = {}
    accord_module._public_keys_loaded = False


class TestDSARDeleteEndpoint:
    """Tests for the POST /accord/dsar/delete endpoint."""

    @pytest.mark.asyncio
    async def test_delete_traces_success(self, client, make_signed_request, public_keys):
        """Successful deletion of existing traces."""
        import main as main_module

        req = make_signed_request()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            "INSERT 0 1",  # INSERT audit record
            "DELETE 5",    # DELETE from accord_traces
            "DELETE 2",    # DELETE from accord_traces_mock
            "DELETE 1",    # DELETE from coherence_ratchet_alerts
            "UPDATE 1",    # UPDATE audit record
        ])
        mock_conn.fetchval = AsyncMock(side_effect=[
            5,    # COUNT(*) from accord_traces
        ])

        mock_pool = _setup_mock_pool(mock_conn)
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value=public_keys)):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["traces_deleted"] == 7
        assert data["details"]["accord_traces"] == 5
        assert data["details"]["mock_traces"] == 2
        assert data["details"]["alerts_cleared"] == 1

    @pytest.mark.asyncio
    async def test_delete_no_traces_found(self, client, make_signed_request, public_keys):
        """No traces found returns 200 with not_found status."""
        import main as main_module

        req = make_signed_request()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            "INSERT 0 1",  # INSERT audit record
            "UPDATE 1",    # UPDATE audit record (no-traces path)
        ])
        mock_conn.fetchval = AsyncMock(side_effect=[
            0,  # COUNT(*) from accord_traces = 0
            0,  # COUNT(*) from accord_traces_mock = 0
        ])

        mock_pool = _setup_mock_pool(mock_conn)
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value=public_keys)):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"
        assert data["traces_deleted"] == 0

    @pytest.mark.asyncio
    async def test_delete_signature_failure(self, client, public_keys, key_id):
        """Invalid signature returns 403."""
        import main as main_module

        req = DSARDeleteRequest(
            agent_id_hash="abc123def456",
            requested_at="2026-03-20T12:00:00+00:00",
            signature="invalid_signature_AAAA",
            signature_key_id=key_id,
        )

        mock_pool = MagicMock()
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value=public_keys)):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        assert response.status_code == 403
        assert "Signature verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_database_unavailable(self, client, make_signed_request):
        """Database unavailable returns 503."""
        import main as main_module

        req = make_signed_request()
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = None
            response = await client.post(
                "/api/v1/accord/dsar/delete",
                json=req.model_dump(),
            )
        finally:
            main_module.db_pool = original_pool

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_delete_no_public_keys_fails_closed(
        self, client, make_signed_request
    ):
        """When no public keys are loaded, deletion is rejected (fail closed)."""
        import main as main_module

        req = make_signed_request()

        mock_pool = MagicMock()
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value={})):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        # Destructive DSAR path must fail closed when keys are unavailable
        assert response.status_code == 403
        assert "Signature verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_audit_trail_recorded(self, client, make_signed_request, public_keys):
        """Verify the DSAR request is recorded in the audit table."""
        import main as main_module

        req = make_signed_request()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            "INSERT 0 1",  # INSERT audit record
            "DELETE 1",    # DELETE from accord_traces
            "DELETE 0",    # DELETE from accord_traces_mock
            "DELETE 0",    # DELETE from coherence_ratchet_alerts
            "UPDATE 1",    # UPDATE audit record
        ])
        mock_conn.fetchval = AsyncMock(side_effect=[
            1,  # COUNT(*) from accord_traces
        ])

        mock_pool = _setup_mock_pool(mock_conn)
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value=public_keys)):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        assert response.status_code == 200

        # Verify INSERT was called for audit trail
        insert_calls = [
            call
            for call in mock_conn.execute.call_args_list
            if "INSERT INTO cirislens.dsar_requests" in str(call)
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_delete_only_mock_traces(self, client, make_signed_request, public_keys):
        """Agent with only mock traces still gets deletion processed."""
        import main as main_module

        req = make_signed_request()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            "INSERT 0 1",  # INSERT audit record
            "UPDATE 1",    # UPDATE audit record (no-traces path)
            "DELETE 0",    # DELETE from accord_traces (none there)
            "DELETE 3",    # DELETE from accord_traces_mock
            "DELETE 0",    # DELETE from coherence_ratchet_alerts
            "UPDATE 1",    # UPDATE audit record (final)
        ])
        mock_conn.fetchval = AsyncMock(side_effect=[
            0,  # COUNT(*) from accord_traces = 0
            3,  # COUNT(*) from accord_traces_mock = 3 (has mock traces)
        ])

        mock_pool = _setup_mock_pool(mock_conn)
        original_pool = main_module.db_pool

        try:
            main_module.db_pool = mock_pool
            with patch("accord_api.load_public_keys", AsyncMock(return_value=public_keys)):
                response = await client.post(
                    "/api/v1/accord/dsar/delete",
                    json=req.model_dump(),
                )
        finally:
            main_module.db_pool = original_pool

        # When accord_traces count is 0 but mock count > 0, it falls through
        # to the deletion path (doesn't return early with not_found)
        assert response.status_code == 200
