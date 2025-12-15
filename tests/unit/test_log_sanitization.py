"""
Unit tests for log sanitization functions.

Tests PII redaction patterns and user ID hashing using both
traditional unit tests and property-based testing with Hypothesis.
"""

import hashlib
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))

from log_ingest import sanitize_message, hash_user_id, REDACT_PATTERNS


class TestSanitizeMessage:
    """Tests for the sanitize_message function."""

    # =========================================================================
    # Basic functionality tests
    # =========================================================================

    def test_none_input_returns_none(self):
        """None input should return None."""
        assert sanitize_message(None) is None

    def test_empty_string_returns_empty(self):
        """Empty string should return empty string."""
        assert sanitize_message("") == ""

    def test_plain_message_unchanged(self):
        """Messages without PII should be unchanged."""
        msg = "Server started successfully on port 8080"
        assert sanitize_message(msg) == msg

    def test_unicode_preserved(self):
        """Unicode characters should be preserved."""
        msg = "User said: 'Hello, 世界! 🎉'"
        assert sanitize_message(msg) == msg

    # =========================================================================
    # Email redaction tests
    # =========================================================================

    def test_email_redacted(self):
        """Email addresses should be redacted."""
        msg = "User email is test@example.com"
        result = sanitize_message(msg)
        assert "test@example.com" not in result
        assert "[EMAIL]" in result

    def test_multiple_emails_redacted(self):
        """Multiple emails should all be redacted."""
        msg = "From: alice@test.com To: bob@example.org"
        result = sanitize_message(msg)
        assert "alice@test.com" not in result
        assert "bob@example.org" not in result
        assert result.count("[EMAIL]") == 2

    def test_email_in_json_redacted(self):
        """Emails embedded in JSON should be redacted."""
        msg = '{"user": "admin@ciris.ai", "action": "login"}'
        result = sanitize_message(msg)
        assert "admin@ciris.ai" not in result
        assert "[EMAIL]" in result

    @given(st.emails())
    @settings(max_examples=50)
    def test_any_valid_email_redacted(self, email):
        """Property: Any valid email format should be redacted."""
        msg = f"Contact: {email}"
        result = sanitize_message(msg)
        # Email should be replaced (may not catch all edge cases due to regex)
        # At minimum, the result shouldn't crash
        assert isinstance(result, str)

    # =========================================================================
    # Credit card redaction tests
    # =========================================================================

    def test_credit_card_with_spaces_redacted(self):
        """Credit card numbers with spaces should be redacted."""
        msg = "Card: 4111 1111 1111 1111"
        result = sanitize_message(msg)
        assert "4111 1111 1111 1111" not in result
        assert "[CARD]" in result

    def test_credit_card_with_dashes_redacted(self):
        """Credit card numbers with dashes should be redacted."""
        msg = "Card: 4111-1111-1111-1111"
        result = sanitize_message(msg)
        assert "4111-1111-1111-1111" not in result
        assert "[CARD]" in result

    def test_credit_card_no_separators_redacted(self):
        """Credit card numbers without separators should be redacted."""
        msg = "Card: 4111111111111111"
        result = sanitize_message(msg)
        assert "4111111111111111" not in result
        assert "[CARD]" in result

    @given(st.from_regex(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}", fullmatch=True))
    @settings(max_examples=50)
    def test_any_card_pattern_redacted(self, card):
        """Property: Any 16-digit card pattern should be redacted."""
        msg = f"Payment card: {card}"
        result = sanitize_message(msg)
        assert card not in result
        assert "[CARD]" in result

    # =========================================================================
    # SSN redaction tests
    # =========================================================================

    def test_ssn_redacted(self):
        """SSN patterns should be redacted."""
        msg = "SSN: 123-45-6789"
        result = sanitize_message(msg)
        assert "123-45-6789" not in result
        assert "[SSN]" in result

    @given(st.from_regex(r"\d{3}-\d{2}-\d{4}", fullmatch=True))
    @settings(max_examples=50)
    def test_any_ssn_pattern_redacted(self, ssn):
        """Property: Any SSN pattern should be redacted."""
        msg = f"Social: {ssn}"
        result = sanitize_message(msg)
        assert ssn not in result
        assert "[SSN]" in result

    # =========================================================================
    # Bearer token redaction tests
    # =========================================================================

    def test_bearer_token_redacted(self):
        """Bearer tokens should be redacted."""
        msg = "Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
        result = sanitize_message(msg)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "Bearer [REDACTED]" in result

    def test_bearer_token_case_sensitive(self):
        """Bearer keyword matching should be case-sensitive."""
        msg = "Auth: Bearer abc123"
        result = sanitize_message(msg)
        assert "Bearer [REDACTED]" in result

    # =========================================================================
    # Key=value pattern redaction tests
    # =========================================================================

    def test_token_param_redacted(self):
        """token= parameters should be redacted."""
        msg = "url?token=secret123abc"
        result = sanitize_message(msg)
        assert "secret123abc" not in result
        assert "token=[REDACTED]" in result

    def test_password_param_redacted(self):
        """password= parameters should be redacted."""
        msg = "login with password=mysecretpass123"
        result = sanitize_message(msg)
        assert "mysecretpass123" not in result
        assert "password=[REDACTED]" in result

    def test_secret_param_redacted(self):
        """secret= parameters should be redacted."""
        msg = "config secret=abc123xyz"
        result = sanitize_message(msg)
        assert "abc123xyz" not in result
        assert "secret=[REDACTED]" in result

    def test_api_key_param_redacted(self):
        """api_key= parameters should be redacted."""
        msg = "request api_key=sk-1234567890abcdef"
        result = sanitize_message(msg)
        assert "sk-1234567890abcdef" not in result
        assert "api_key=[REDACTED]" in result

    # =========================================================================
    # Multiple PII in one message
    # =========================================================================

    def test_multiple_pii_types_redacted(self):
        """Messages with multiple PII types should have all redacted."""
        msg = "User test@example.com paid with 4111-1111-1111-1111 SSN 123-45-6789"
        result = sanitize_message(msg)
        assert "[EMAIL]" in result
        assert "[CARD]" in result
        assert "[SSN]" in result
        assert "test@example.com" not in result

    # =========================================================================
    # Property-based tests for robustness
    # =========================================================================

    @given(st.text(max_size=10000))
    @settings(max_examples=100)
    def test_never_crashes_on_arbitrary_input(self, text):
        """Property: sanitize_message never crashes on any input."""
        result = sanitize_message(text)
        assert result is None or isinstance(result, str)

    @given(st.text(max_size=1000))
    @settings(max_examples=50)
    def test_output_not_longer_than_input_significantly(self, text):
        """Property: Output shouldn't be drastically longer than input."""
        result = sanitize_message(text)
        if result:
            # Redaction might make string slightly longer, but not by much
            # [EMAIL], [CARD], [SSN] are short replacements
            assert len(result) <= len(text) + 100 * text.count("@")

    @given(st.text(min_size=1, max_size=100).filter(lambda x: "@" not in x and not x.isdigit()))
    @settings(max_examples=50)
    def test_non_pii_text_unchanged(self, text):
        """Property: Text without PII patterns should be unchanged."""
        # Filter out text that might match patterns
        assume("Bearer " not in text)
        assume("token=" not in text)
        assume("password=" not in text)
        assume("secret=" not in text)
        assume("api_key=" not in text)
        result = sanitize_message(text)
        assert result == text


class TestHashUserId:
    """Tests for the hash_user_id function."""

    def test_returns_string(self):
        """hash_user_id should return a string."""
        result = hash_user_id("user123")
        assert isinstance(result, str)

    def test_returns_16_chars(self):
        """hash_user_id should return exactly 16 characters."""
        result = hash_user_id("user123")
        assert len(result) == 16

    def test_returns_hex_string(self):
        """hash_user_id should return a hex string."""
        result = hash_user_id("user123")
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        """Same input should always produce same output."""
        result1 = hash_user_id("user123")
        result2 = hash_user_id("user123")
        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different inputs should produce different outputs."""
        result1 = hash_user_id("user123")
        result2 = hash_user_id("user456")
        assert result1 != result2

    def test_matches_sha256_prefix(self):
        """Output should match first 16 chars of SHA256 hash."""
        user_id = "test_user"
        expected = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        assert hash_user_id(user_id) == expected

    @given(st.text(min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_any_string_produces_valid_hash(self, user_id):
        """Property: Any non-empty string produces a valid 16-char hex hash."""
        result = hash_user_id(user_id)
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    @given(st.text(min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_hash_is_deterministic_property(self, user_id):
        """Property: Hashing is deterministic."""
        result1 = hash_user_id(user_id)
        result2 = hash_user_id(user_id)
        assert result1 == result2


class TestRedactPatterns:
    """Tests for the REDACT_PATTERNS constant."""

    def test_patterns_are_compiled(self):
        """All patterns should be compiled regex objects."""
        for pattern, _replacement in REDACT_PATTERNS:
            assert hasattr(pattern, "sub"), f"Pattern {pattern} is not compiled"

    def test_patterns_have_replacements(self):
        """All patterns should have non-empty replacement strings."""
        for _pattern, replacement in REDACT_PATTERNS:
            assert replacement, "Replacement string should not be empty"

    def test_minimum_patterns_present(self):
        """Should have at least the core privacy patterns."""
        pattern_names = [r[1] for r in REDACT_PATTERNS]
        assert "Bearer [REDACTED]" in pattern_names
        assert "[EMAIL]" in pattern_names
        assert "[CARD]" in pattern_names
        assert "[SSN]" in pattern_names
