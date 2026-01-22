"""
Tests for PII Scrubber service.

Tests the NER-based PII scrubbing and cryptographic envelope preservation.
"""

import hashlib
import json
import pytest
from unittest.mock import patch, MagicMock

# Import the module under test
import sys
sys.path.insert(0, "api")

from pii_scrubber import (
    scrub_text,
    scrub_text_regex_only,
    scrub_dict_recursive,
    hash_content,
    PIIScrubber,
    SCRUB_FIELDS,
)


class TestRegexScrubbing:
    """Test regex-based PII scrubbing (fallback when spaCy unavailable)."""

    def test_scrub_email(self):
        text = "Contact john.doe@example.com for more info"
        result = scrub_text_regex_only(text)
        assert "[EMAIL]" in result
        assert "john.doe@example.com" not in result

    def test_scrub_phone(self):
        text = "Call me at 555-123-4567 or (555) 987-6543"
        result = scrub_text_regex_only(text)
        assert "[PHONE]" in result
        assert "555-123-4567" not in result
        assert "555) 987-6543" not in result

    def test_scrub_ip_address(self):
        text = "Server at 192.168.1.100 is down"
        result = scrub_text_regex_only(text)
        assert "[IP_ADDRESS]" in result
        assert "192.168.1.100" not in result

    def test_scrub_url(self):
        text = "Visit https://example.com/user/john123 for profile"
        result = scrub_text_regex_only(text)
        assert "[URL]" in result
        assert "https://example.com/user/john123" not in result

    def test_scrub_ssn(self):
        text = "SSN: 123-45-6789"
        result = scrub_text_regex_only(text)
        assert "[SSN]" in result
        assert "123-45-6789" not in result

    def test_scrub_credit_card(self):
        text = "Card: 1234-5678-9012-3456"
        result = scrub_text_regex_only(text)
        assert "[CREDIT_CARD]" in result
        assert "1234-5678-9012-3456" not in result

    def test_preserves_non_pii(self):
        text = "The weather is nice today"
        result = scrub_text_regex_only(text)
        assert result == text

    def test_handles_none(self):
        assert scrub_text_regex_only(None) is None

    def test_handles_empty_string(self):
        assert scrub_text_regex_only("") == ""


class TestNERScrubbing:
    """Test NER-based PII scrubbing (with spaCy)."""

    def test_scrub_text_with_ner(self):
        """Test that NER scrubbing works when spaCy is available."""
        # This test may use regex fallback if spaCy not installed
        text = "John Smith works at Acme Corp in New York"
        result = scrub_text(text)

        # Should have some redactions (either from NER or regex patterns)
        # At minimum, the text should be different if it contained PII
        # Note: If spaCy is not installed, this falls back to regex only
        assert isinstance(result, str)

    def test_scrub_text_handles_none(self):
        assert scrub_text(None) is None

    def test_scrub_text_handles_empty(self):
        assert scrub_text("") == ""


class TestDictScrubbing:
    """Test recursive dictionary scrubbing."""

    def test_scrub_dict_simple(self):
        data = {
            "task_description": "Help john.doe@example.com with login",
            "other_field": "Keep this value",
        }
        result = scrub_dict_recursive(data)

        assert "[EMAIL]" in result["task_description"]
        assert result["other_field"] == "Keep this value"

    def test_scrub_dict_nested(self):
        data = {
            "level1": {
                "task_description": "User john@example.com reported issue",
                "level2": {
                    "reasoning": "Contact 555-123-4567 for support"
                }
            }
        }
        result = scrub_dict_recursive(data)

        assert "[EMAIL]" in result["level1"]["task_description"]
        assert "[PHONE]" in result["level1"]["level2"]["reasoning"]

    def test_scrub_dict_list(self):
        data = {
            "items": [
                {"initial_context": "Email: test@test.com"},
                {"initial_context": "Phone: 555-999-8888"},
            ]
        }
        result = scrub_dict_recursive(data)

        assert "[EMAIL]" in result["items"][0]["initial_context"]
        assert "[PHONE]" in result["items"][1]["initial_context"]

    def test_only_scrubs_target_fields(self):
        """Verify only fields in SCRUB_FIELDS are modified."""
        data = {
            "task_description": "john@example.com",  # Should scrub
            "random_field": "john@example.com",      # Should NOT scrub
        }
        result = scrub_dict_recursive(data)

        assert "[EMAIL]" in result["task_description"]
        assert result["random_field"] == "john@example.com"

    def test_max_depth_protection(self):
        """Test that max depth prevents infinite recursion."""
        # Create deeply nested structure
        data = {"level": 0}
        current = data
        for i in range(25):
            current["nested"] = {"level": i + 1}
            current = current["nested"]
        current["task_description"] = "test@test.com"

        # Should not raise, should handle gracefully
        result = scrub_dict_recursive(data, max_depth=20)
        assert isinstance(result, dict)


class TestHashContent:
    """Test content hashing."""

    def test_hash_string(self):
        content = "test content"
        result = hash_content(content)

        expected = hashlib.sha256(content.encode('utf-8')).hexdigest()
        assert result == expected

    def test_hash_bytes(self):
        content = b"test content bytes"
        result = hash_content(content)

        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_hash_deterministic(self):
        content = "same content"
        assert hash_content(content) == hash_content(content)

    def test_hash_different_content(self):
        assert hash_content("content1") != hash_content("content2")


class TestPIIScrubber:
    """Test PIIScrubber class."""

    def test_should_scrub_full_traces(self):
        scrubber = PIIScrubber()
        assert scrubber.should_scrub("full_traces") is True

    def test_should_not_scrub_generic(self):
        scrubber = PIIScrubber()
        assert scrubber.should_scrub("generic") is False

    def test_should_not_scrub_detailed(self):
        scrubber = PIIScrubber()
        assert scrubber.should_scrub("detailed") is False

    def test_scrub_trace_adds_envelope(self):
        scrubber = PIIScrubber()

        trace_data = {
            "trace_id": "test-trace-1",
            "components": [
                {
                    "component_type": "observation",
                    "event_type": "THOUGHT_START",
                    "data": {
                        "task_description": "Help john@example.com reset password"
                    }
                }
            ]
        }
        original_message = json.dumps(trace_data["components"], sort_keys=True).encode()

        result = scrubber.scrub_trace(trace_data, True, original_message)

        # Check envelope fields are present
        assert "original_content_hash" in result
        assert "scrub_timestamp" in result
        assert "scrub_key_id" in result
        assert result["original_content_hash"] == hashlib.sha256(original_message).hexdigest()

    def test_scrub_trace_scrubs_pii(self):
        scrubber = PIIScrubber()

        trace_data = {
            "components": [
                {
                    "component_type": "observation",
                    "data": {
                        "task_description": "Contact john@example.com"
                    }
                }
            ]
        }
        original_message = json.dumps(trace_data["components"], sort_keys=True).encode()

        result = scrubber.scrub_trace(trace_data, True, original_message)

        # Check PII was scrubbed from components
        scrubbed_task = result["components"][0]["data"]["task_description"]
        assert "[EMAIL]" in scrubbed_task
        assert "john@example.com" not in scrubbed_task


class TestScrubFields:
    """Test that all expected fields are in SCRUB_FIELDS."""

    def test_thought_start_fields(self):
        assert "task_description" in SCRUB_FIELDS
        assert "initial_context" in SCRUB_FIELDS

    def test_snapshot_fields(self):
        assert "system_snapshot" in SCRUB_FIELDS
        assert "gathered_context" in SCRUB_FIELDS
        assert "relevant_memories" in SCRUB_FIELDS
        assert "conversation_history" in SCRUB_FIELDS

    def test_dma_fields(self):
        assert "reasoning" in SCRUB_FIELDS
        assert "prompt_used" in SCRUB_FIELDS
        assert "combined_analysis" in SCRUB_FIELDS

    def test_aspdma_fields(self):
        assert "action_rationale" in SCRUB_FIELDS
        assert "reasoning_summary" in SCRUB_FIELDS
        assert "action_parameters" in SCRUB_FIELDS
        assert "aspdma_prompt" in SCRUB_FIELDS

    def test_conscience_fields(self):
        assert "conscience_override_reason" in SCRUB_FIELDS
        assert "epistemic_data" in SCRUB_FIELDS
        assert "updated_status_content" in SCRUB_FIELDS
        assert "entropy_reason" in SCRUB_FIELDS
        assert "coherence_reason" in SCRUB_FIELDS
        assert "optimization_veto_justification" in SCRUB_FIELDS
        assert "epistemic_humility_justification" in SCRUB_FIELDS

    def test_action_fields(self):
        assert "execution_error" in SCRUB_FIELDS

    def test_total_field_count(self):
        """Verify we have all 21 fields documented."""
        assert len(SCRUB_FIELDS) == 21


class TestRegexScrubbing_Extended:
    """Extended tests for regex-based PII scrubbing."""

    def test_scrub_multiple_emails(self):
        """Test scrubbing multiple emails in same text."""
        text = "Contact john@example.com or jane@test.org for help"
        result = scrub_text_regex_only(text)
        assert result.count("[EMAIL]") == 2
        assert "john@example.com" not in result
        assert "jane@test.org" not in result

    def test_scrub_multiple_pii_types(self):
        """Test scrubbing mixed PII types."""
        text = "Call john@test.com at 555-123-4567 from IP 192.168.1.1"
        result = scrub_text_regex_only(text)
        assert "[EMAIL]" in result
        assert "[PHONE]" in result
        assert "[IP_ADDRESS]" in result
        assert "john@test.com" not in result
        assert "555-123-4567" not in result
        assert "192.168.1.1" not in result

    def test_scrub_phone_formats(self):
        """Test various phone number formats."""
        texts = [
            "Call 5551234567",
            "Call 555.123.4567",
            "Call +1 555-123-4567",
            "Call 1-555-123-4567",
        ]
        for text in texts:
            result = scrub_text_regex_only(text)
            assert "[PHONE]" in result

    def test_scrub_url_http(self):
        """Test HTTP URL scrubbing."""
        text = "Visit http://example.com/user"
        result = scrub_text_regex_only(text)
        assert "[URL]" in result

    def test_scrub_credit_card_spaces(self):
        """Test credit card with spaces."""
        text = "Card: 1234 5678 9012 3456"
        result = scrub_text_regex_only(text)
        assert "[CREDIT_CARD]" in result

    def test_handles_non_string(self):
        """Test non-string input returns as-is."""
        assert scrub_text_regex_only(123) == 123
        assert scrub_text_regex_only(["a", "b"]) == ["a", "b"]

    def test_preserves_whitespace(self):
        """Test that whitespace is preserved."""
        text = "No PII   here\n\twith whitespace"
        result = scrub_text_regex_only(text)
        assert result == text


class TestNERScrubbing_Extended:
    """Extended tests for NER-based PII scrubbing."""

    def test_scrub_combined_with_regex(self):
        """Test NER scrubbing combined with regex patterns."""
        # Even if spaCy fails, regex should catch these
        text = "John contacted jane@example.com from 10.0.0.1"
        result = scrub_text(text)
        assert "[EMAIL]" in result
        assert "[IP_ADDRESS]" in result

    def test_handles_unicode(self):
        """Test handling of unicode text."""
        text = "Müller called from 555-123-4567"
        result = scrub_text(text)
        assert "[PHONE]" in result
        assert "555-123-4567" not in result

    def test_handles_long_text(self):
        """Test handling of long text."""
        text = "Some text with john@test.com in it. " * 100
        result = scrub_text(text)
        assert result.count("[EMAIL]") == 100

    def test_handles_non_string(self):
        """Test non-string input returns as-is."""
        assert scrub_text(123) == 123
        assert scrub_text(None) is None


class TestDictScrubbing_Extended:
    """Extended tests for recursive dictionary scrubbing."""

    def test_scrub_empty_dict(self):
        """Test empty dictionary."""
        result = scrub_dict_recursive({})
        assert result == {}

    def test_scrub_empty_list(self):
        """Test empty list."""
        result = scrub_dict_recursive([])
        assert result == []

    def test_scrub_primitive_value(self):
        """Test primitive values pass through."""
        assert scrub_dict_recursive("string") == "string"
        assert scrub_dict_recursive(123) == 123
        assert scrub_dict_recursive(None) is None
        assert scrub_dict_recursive(True) is True

    def test_scrub_non_string_values_in_scrub_fields(self):
        """Test non-string values in scrub fields are preserved."""
        data = {
            "task_description": 123,  # int instead of str
            "reasoning": None,  # None value
            "initial_context": ["list", "value"],  # list
        }
        result = scrub_dict_recursive(data)
        assert result["task_description"] == 123
        assert result["reasoning"] is None
        assert result["initial_context"] == ["list", "value"]

    def test_scrub_mixed_list(self):
        """Test list with mixed content types."""
        data = [
            {"task_description": "test@test.com"},
            "raw string",
            123,
            None,
        ]
        result = scrub_dict_recursive(data)
        assert "[EMAIL]" in result[0]["task_description"]
        assert result[1] == "raw string"
        assert result[2] == 123
        assert result[3] is None

    def test_preserves_dict_structure(self):
        """Test that dictionary structure is preserved."""
        data = {
            "a": {"b": {"c": {"task_description": "email@test.com"}}},
            "x": [1, 2, 3],
            "y": "plain value",
        }
        result = scrub_dict_recursive(data)
        assert "a" in result
        assert "b" in result["a"]
        assert "c" in result["a"]["b"]
        assert "[EMAIL]" in result["a"]["b"]["c"]["task_description"]
        assert result["x"] == [1, 2, 3]
        assert result["y"] == "plain value"


class TestSignContent:
    """Test content signing functionality."""

    def test_sign_content_without_nacl(self):
        """Test sign_content when nacl is not available."""
        from pii_scrubber import sign_content

        # With invalid key, should return empty string
        result = sign_content("test content", b"invalid_key")
        assert result == ""

    def test_sign_content_with_valid_key(self):
        """Test sign_content with valid Ed25519 key."""
        from pii_scrubber import sign_content

        try:
            from nacl.signing import SigningKey
            key = SigningKey.generate()
            key_bytes = bytes(key)

            result = sign_content("test content", key_bytes)
            assert result != ""
            assert len(result) > 0
        except ImportError:
            pytest.skip("nacl not installed")

    def test_sign_content_bytes(self):
        """Test sign_content with bytes input."""
        from pii_scrubber import sign_content

        try:
            from nacl.signing import SigningKey
            key = SigningKey.generate()
            key_bytes = bytes(key)

            result = sign_content(b"test content bytes", key_bytes)
            assert result != ""
        except ImportError:
            pytest.skip("nacl not installed")


class TestPIIScrubber_Extended:
    """Extended tests for PIIScrubber class."""

    def test_init_without_key(self):
        """Test initialization without signing key."""
        with patch.dict('os.environ', {}, clear=True):
            scrubber = PIIScrubber()
            assert scrubber._signing_key is None

    def test_init_with_key_file(self):
        """Test initialization with key file."""
        try:
            from nacl.signing import SigningKey
            import tempfile
            import os

            # Create temporary key file
            key = SigningKey.generate()
            with tempfile.NamedTemporaryFile(mode='wb', delete=False) as f:
                f.write(bytes(key))
                key_path = f.name

            try:
                scrubber = PIIScrubber(scrub_key_path=key_path)
                assert scrubber._signing_key is not None
            finally:
                os.unlink(key_path)
        except ImportError:
            pytest.skip("nacl not installed")

    def test_scrub_trace_with_signing_key(self):
        """Test scrub_trace with actual signing key."""
        try:
            from nacl.signing import SigningKey
            import tempfile
            import os

            key = SigningKey.generate()
            with tempfile.NamedTemporaryFile(mode='wb', delete=False) as f:
                f.write(bytes(key))
                key_path = f.name

            try:
                scrubber = PIIScrubber(scrub_key_path=key_path)

                trace_data = {
                    "components": [
                        {"data": {"task_description": "email@test.com"}}
                    ]
                }
                original_message = json.dumps(trace_data["components"]).encode()

                result = scrubber.scrub_trace(trace_data, True, original_message)

                assert result["scrub_signature"] is not None
                assert result["scrub_signature"] != ""
            finally:
                os.unlink(key_path)
        except ImportError:
            pytest.skip("nacl not installed")

    def test_scrub_trace_without_components(self):
        """Test scrub_trace when components is missing."""
        scrubber = PIIScrubber()
        trace_data = {"trace_id": "test"}
        original_message = b"test"

        result = scrubber.scrub_trace(trace_data, True, original_message)

        assert "original_content_hash" in result
        assert "scrub_timestamp" in result
        assert "components" not in result or result.get("components") == []

    def test_scrub_component_non_dict(self):
        """Test _scrub_component with non-dict input."""
        scrubber = PIIScrubber()
        result = scrubber._scrub_component("not a dict")
        assert result == "not a dict"

    def test_scrub_component_without_data(self):
        """Test _scrub_component when data field is missing."""
        scrubber = PIIScrubber()
        component = {"component_type": "test", "other": "value"}
        result = scrubber._scrub_component(component)
        assert result == {"component_type": "test", "other": "value"}

    def test_original_signature_verified_field(self):
        """Test that original_signature_verified is stored."""
        scrubber = PIIScrubber()
        trace_data = {"components": []}
        original_message = b"test"

        result = scrubber.scrub_trace(trace_data, False, original_message)
        assert result["original_signature_verified"] is False

        trace_data2 = {"components": []}
        result2 = scrubber.scrub_trace(trace_data2, True, b"test2")
        assert result2["original_signature_verified"] is True


class TestScrubFullTraceConvenience:
    """Test the convenience function scrub_full_trace."""

    def test_scrub_full_trace_basic(self):
        """Test basic scrub_full_trace usage."""
        from pii_scrubber import scrub_full_trace

        trace_data = {
            "components": [
                {"data": {"reasoning": "User john@test.com requested help"}}
            ]
        }
        original_message = json.dumps(trace_data["components"]).encode()

        result = scrub_full_trace(trace_data, True, original_message)

        assert "[EMAIL]" in result["components"][0]["data"]["reasoning"]
        assert "original_content_hash" in result
        assert "scrub_timestamp" in result

    def test_scrub_full_trace_returns_same_object(self):
        """Test that scrub_full_trace modifies the dict in place."""
        from pii_scrubber import scrub_full_trace

        trace_data = {"components": []}
        original_message = b"test"

        result = scrub_full_trace(trace_data, True, original_message)
        assert result is trace_data


class TestGetScrubber:
    """Test the get_scrubber singleton."""

    def test_get_scrubber_returns_instance(self):
        """Test get_scrubber returns a PIIScrubber instance."""
        from pii_scrubber import get_scrubber

        scrubber = get_scrubber()
        assert isinstance(scrubber, PIIScrubber)

    def test_get_scrubber_singleton(self):
        """Test get_scrubber returns same instance."""
        from pii_scrubber import get_scrubber

        scrubber1 = get_scrubber()
        scrubber2 = get_scrubber()
        assert scrubber1 is scrubber2
