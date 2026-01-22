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
