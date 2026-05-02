"""
Tests for the federation_keys mirror in `api/federation_mirror.py`.

Covers the additive directory write that runs alongside the existing
accord_public_keys INSERT in `register_public_key`:

- Steward-not-configured short-circuits to no-op (no Engine call)
- Engine-not-initialized short-circuits to no-op
- Successful path canonicalizes envelope, signs with steward,
  submits a SignedKeyRecord with the right shape
- Engine raising in any phase is swallowed (best-effort, returns False)
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))


@pytest.fixture
def stub_engine():
    """Engine stub with the three v0.2.2 federation methods. Tests
    override individual return values / side_effects per case."""
    engine = MagicMock()
    engine.canonicalize_envelope.return_value = b'{"canonical":"bytes"}'
    engine.steward_sign.return_value = b"\x01" * 64
    engine.steward_key_id.return_value = "lens-steward"
    engine.put_public_key.return_value = None
    return engine


# ─── short-circuit paths ───────────────────────────────────────────


class TestShortCircuit:
    def test_steward_not_ready_returns_false_without_engine_call(self):
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=False), \
             patch.object(persist_engine, "get_engine") as get_engine:
            result = mirror_agent_registration(
                key_id="agent-foo",
                public_key_base64="A" * 44,
                description=None,
            )
        assert result is False
        # Important: no Engine method called when steward unconfigured —
        # otherwise an Engine raising "no steward key" would noisily
        # log a warning on every registration.
        get_engine.assert_not_called()

    def test_engine_none_returns_false(self):
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=None):
            result = mirror_agent_registration(
                key_id="agent-foo",
                public_key_base64="A" * 44,
                description=None,
            )
        assert result is False


# ─── successful mirror path ────────────────────────────────────────


class TestSuccessPath:
    def test_envelope_canonicalized_via_engine(self, stub_engine):
        """The mirror MUST use engine.canonicalize_envelope rather than
        re-implementing the canonicalization client-side. Re-implementing
        is the CIRISPersist#7 drift trap that bit float-formatting.
        Verifies the engine method is the one called with the JSON shape."""
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            result = mirror_agent_registration(
                key_id="agent-bar",
                public_key_base64="B" * 44,
                description="test agent",
            )
        assert result is True
        stub_engine.canonicalize_envelope.assert_called_once()
        envelope_json = stub_engine.canonicalize_envelope.call_args[0][0]
        envelope = json.loads(envelope_json)
        assert envelope["registrar"] == "lens"
        assert envelope["role"] == "agent_trace_signing"
        assert envelope["key_id"] == "agent-bar"
        assert envelope["pubkey_ed25519_base64"] == "B" * 44
        assert envelope["description"] == "test agent"
        assert "registered_at" in envelope

    def test_steward_sign_called_with_canonical_bytes(self, stub_engine):
        """The signature is over the canonical bytes from persist —
        not the raw envelope JSON. If the lens accidentally signed
        something else, persist's later verify-on-lookup would reject
        the row's scrub envelope."""
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            mirror_agent_registration(
                key_id="agent-baz",
                public_key_base64="C" * 44,
                description=None,
            )
        stub_engine.steward_sign.assert_called_once_with(b'{"canonical":"bytes"}')

    def test_put_public_key_called_with_correct_shape(self, stub_engine):
        """The SignedKeyRecord JSON shape must match persist's serde
        deserializer. Field-level checks on the resulting record."""
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            mirror_agent_registration(
                key_id="agent-qux",
                public_key_base64="D" * 44,
                description=None,
            )
        stub_engine.put_public_key.assert_called_once()
        signed_record_json = stub_engine.put_public_key.call_args[0][0]
        signed_record = json.loads(signed_record_json)
        record = signed_record["record"]

        # Identity classification
        assert record["key_id"] == "agent-qux"
        assert record["pubkey_ed25519_base64"] == "D" * 44
        assert record["algorithm"] == "hybrid"  # schema-enforced
        assert record["identity_type"] == "agent"
        assert record["identity_ref"] == "agent-qux"

        # Scrub envelope
        assert record["scrub_key_id"] == "lens-steward"
        # Signature is base64 of 64 \x01 bytes
        expected_sig = base64.b64encode(b"\x01" * 64).decode("ascii")
        assert record["scrub_signature_classical"] == expected_sig
        # 64 bytes of \x01 → b64 → 88 chars (with one '=' pad)
        assert len(record["scrub_signature_classical"]) == 88

        # PQC fields hybrid-pending
        assert "scrub_signature_pqc" not in record or record["scrub_signature_pqc"] is None
        assert "pubkey_ml_dsa_65_base64" not in record or record["pubkey_ml_dsa_65_base64"] is None

        # Server-computed fields ignored on write
        assert record["persist_row_hash"] == ""

    def test_description_omitted_when_none(self, stub_engine):
        """Operator description is optional — None means no entry in
        the canonical envelope (cleaner than a literal null field)."""
        import persist_engine
        from federation_mirror import mirror_agent_registration

        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            mirror_agent_registration(
                key_id="agent-no-desc",
                public_key_base64="E" * 44,
                description=None,
            )
        envelope_json = stub_engine.canonicalize_envelope.call_args[0][0]
        envelope = json.loads(envelope_json)
        assert "description" not in envelope


# ─── failure swallowing (best-effort) ──────────────────────────────


class TestBestEffort:
    """Mirror failures must NOT raise — the legacy accord_public_keys
    INSERT is the load-bearing write, and verify still works in the
    failure mode via persist's dual-read fallback."""

    def test_canonicalize_failure_swallowed(self, stub_engine):
        import persist_engine
        from federation_mirror import mirror_agent_registration

        stub_engine.canonicalize_envelope.side_effect = ValueError("bad shape")
        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            result = mirror_agent_registration(
                key_id="agent-x",
                public_key_base64="F" * 44,
                description=None,
            )
        assert result is False  # logged but not raised

    def test_steward_sign_failure_swallowed(self, stub_engine):
        import persist_engine
        from federation_mirror import mirror_agent_registration

        stub_engine.steward_sign.side_effect = RuntimeError("keyring unreachable")
        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            result = mirror_agent_registration(
                key_id="agent-y",
                public_key_base64="G" * 44,
                description=None,
            )
        assert result is False

    def test_put_public_key_failure_swallowed(self, stub_engine):
        """The most likely failure mode in production: bridge hasn't
        run the bootstrap script yet, so the lens-steward row doesn't
        exist in federation_keys, so the FK constraint on scrub_key_id
        rejects every put_public_key call. The mirror returns False;
        the registration POST returns 200 because accord_public_keys
        already succeeded."""
        import persist_engine
        from federation_mirror import mirror_agent_registration

        stub_engine.put_public_key.side_effect = RuntimeError(
            "FK violation: scrub_key_id='lens-steward' not in federation_keys"
        )
        with patch.object(persist_engine, "steward_ready", return_value=True), \
             patch.object(persist_engine, "get_engine", return_value=stub_engine):
            result = mirror_agent_registration(
                key_id="agent-z",
                public_key_base64="H" * 44,
                description=None,
            )
        assert result is False
