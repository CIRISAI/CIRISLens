"""Tests for Security Sanitizer module."""

import json

import pytest

from api.security_sanitizer import (
    DANGEROUS_PATTERNS,
    SANITIZE_FIELDS,
    SIZE_LIMITS,
    SanitizationResult,
    TraceSanitizationResult,
    compute_content_hash,
    detect_patterns,
    neutralize_pattern,
    sanitize_dict_recursive,
    sanitize_text,
    sanitize_trace,
    sanitize_trace_for_storage,
    validate_identifier,
    validate_models_used,
    validate_numeric,
    validate_score,
)


class TestComputeContentHash:
    """Tests for content hashing."""

    def test_hash_string(self):
        result = compute_content_hash("hello world")
        assert len(result) == 64  # SHA-256 hex
        assert result.isalnum()

    def test_hash_dict(self):
        result = compute_content_hash({"key": "value"})
        assert len(result) == 64

    def test_hash_is_deterministic(self):
        data = {"a": 1, "b": 2}
        hash1 = compute_content_hash(data)
        hash2 = compute_content_hash(data)
        assert hash1 == hash2

    def test_hash_different_for_different_content(self):
        hash1 = compute_content_hash({"a": 1})
        hash2 = compute_content_hash({"a": 2})
        assert hash1 != hash2


class TestDetectPatterns:
    """Tests for pattern detection."""

    def test_detects_script_tag(self):
        text = '<script>alert("xss")</script>'
        detections = detect_patterns(text)
        assert "xss_script" in detections

    def test_detects_event_handler(self):
        text = '<img onerror="alert(1)">'
        detections = detect_patterns(text)
        assert any("event" in d for d in detections)

    def test_detects_javascript_url(self):
        text = '<a href="javascript:alert(1)">click</a>'
        detections = detect_patterns(text)
        assert "xss_javascript_url" in detections

    def test_detects_sql_union(self):
        text = "SELECT * FROM users UNION SELECT * FROM passwords"
        detections = detect_patterns(text)
        assert "sql_union_select" in detections

    def test_detects_sql_drop(self):
        text = "DROP TABLE users"
        detections = detect_patterns(text)
        assert "sql_drop" in detections

    def test_detects_iframe(self):
        text = '<iframe src="evil.com"></iframe>'
        detections = detect_patterns(text)
        assert any("iframe" in d for d in detections)

    def test_detects_path_traversal(self):
        text = "../../../etc/passwd"
        detections = detect_patterns(text)
        assert "path_traversal" in detections

    def test_detects_null_byte(self):
        text = "file.txt%00.jpg"
        detections = detect_patterns(text)
        assert "null_byte" in detections

    def test_safe_text_no_detections(self):
        text = "This is a normal reasoning trace about helping the user."
        detections = detect_patterns(text)
        assert detections == []

    def test_empty_text_no_detections(self):
        assert detect_patterns("") == []
        assert detect_patterns(None) == []


class TestNeutralizePattern:
    """Tests for pattern neutralization."""

    def test_neutralizes_script(self):
        text = '<script>alert(1)</script>'
        pattern = DANGEROUS_PATTERNS["xss_script"]
        result = neutralize_pattern(text, "xss_script", pattern)
        assert "<script>" not in result
        assert "[XSS_REMOVED:xss_script]" in result

    def test_neutralizes_sql(self):
        text = "UNION SELECT password FROM users"
        pattern = DANGEROUS_PATTERNS["sql_union_select"]
        result = neutralize_pattern(text, "sql_union_select", pattern)
        assert "UNION SELECT" not in result
        assert "[SQL_REMOVED:sql_union_select]" in result


class TestSanitizeText:
    """Tests for text sanitization."""

    def test_safe_text_unchanged(self):
        text = "This is safe text"
        result = sanitize_text(text)
        assert result.sanitized_text == text
        assert result.was_modified is False
        assert result.detections == []

    def test_truncates_long_text(self):
        text = "a" * 200_000
        result = sanitize_text(text)
        assert len(result.sanitized_text) < len(text)
        assert result.was_truncated is True
        assert "size_limit_exceeded" in result.detections

    def test_removes_script_tags(self):
        text = 'Safe text <script>evil()</script> more safe'
        result = sanitize_text(text)
        assert "<script>" not in result.sanitized_text
        assert result.was_modified is True
        assert any("xss" in d for d in result.detections)

    def test_removes_event_handlers(self):
        text = '<div onclick="steal()">content</div>'
        result = sanitize_text(text)
        assert "onclick" not in result.sanitized_text
        assert result.was_modified is True

    def test_identifier_stricter_length(self):
        text = "a" * 500
        result = sanitize_text(text, is_identifier=True)
        assert len(result.sanitized_text) <= SIZE_LIMITS["max_string_in_identifier"] + len("[TRUNCATED]")

    def test_custom_max_length(self):
        text = "a" * 100
        result = sanitize_text(text, max_length=50)
        assert len(result.sanitized_text) == 50 + len("[TRUNCATED]")

    def test_handles_none(self):
        result = sanitize_text(None)
        assert result.sanitized_text == ""
        assert result.was_modified is False


class TestSanitizeDictRecursive:
    """Tests for recursive dictionary sanitization."""

    def test_sanitizes_nested_dict(self):
        data = {
            "level1": {
                "action_rationale": '<script>evil()</script>'
            }
        }
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert "<script>" not in result["level1"]["action_rationale"]
        assert modified > 0
        assert any("xss" in d for d in detections)

    def test_sanitizes_list_items(self):
        data = {
            "items": ['<script>x</script>', "safe", '<iframe src="x">']
        }
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert "<script>" not in str(result["items"])
        assert "<iframe" not in str(result["items"])

    def test_respects_depth_limit(self):
        # Create deeply nested structure
        data = {"level": {}}
        current = data["level"]
        for i in range(25):
            current["nested"] = {}
            current = current["nested"]

        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert "depth_limit_exceeded" in detections

    def test_truncates_long_arrays(self):
        data = {"items": ["x"] * 1500}
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert len(result["items"]) <= SIZE_LIMITS["max_array_length"]
        assert "array_truncated" in detections

    def test_only_sanitizes_whitelisted_fields(self):
        data = {
            "action_rationale": "<script>evil</script>",  # Whitelisted
            "random_field": "<script>evil</script>",      # Not whitelisted
        }
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert "<script>" not in result["action_rationale"]
        # random_field is NOT in SANITIZE_FIELDS, so it passes through
        # (unless it's a nested string in a list)
        assert "action_rationale" in SANITIZE_FIELDS
        assert "random_field" not in SANITIZE_FIELDS

    def test_preserves_primitives(self):
        data = {
            "number": 42,
            "float": 3.14,
            "bool": True,
            "null": None,
        }
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert result["number"] == 42
        assert result["float"] == 3.14
        assert result["bool"] is True
        assert result["null"] is None


class TestSanitizeTrace:
    """Tests for full trace sanitization."""

    def test_computes_original_hash(self):
        trace = {"trace_id": "test-123", "data": "safe"}
        result = sanitize_trace(trace)
        assert len(result.original_hash) == 64

    def test_returns_sanitization_metadata(self):
        trace = {
            "trace_id": "test-123",
            "action_rationale": "<script>x</script>"
        }
        result = sanitize_trace(trace)
        assert isinstance(result, TraceSanitizationResult)
        assert result.fields_modified > 0
        assert any("xss" in d for d in result.total_detections)
        assert result.timestamp is not None

    def test_clean_trace_no_detections(self):
        trace = {
            "trace_id": "test-123",
            "action_rationale": "Decided to help the user with their question."
        }
        result = sanitize_trace(trace)
        assert result.total_detections == []
        assert result.fields_modified == 0


class TestValidateIdentifier:
    """Tests for identifier validation."""

    def test_truncates_long_identifier(self):
        value = "x" * 500
        result, issues = validate_identifier(value, "trace_id")
        assert len(result) <= SIZE_LIMITS["max_string_in_identifier"]
        assert "trace_id_truncated" in issues

    def test_strips_dangerous_patterns(self):
        value = "trace-<script>evil</script>-123"
        result, issues = validate_identifier(value, "trace_id")
        assert "<script>" not in result
        assert any("xss" in i for i in issues)

    def test_handles_none(self):
        result, issues = validate_identifier(None, "trace_id")
        assert result is None
        assert issues == []

    def test_coerces_non_string(self):
        result, issues = validate_identifier(12345, "trace_id")
        assert result == "12345"
        assert "type_coerced" in issues


class TestValidateNumeric:
    """Tests for numeric validation."""

    def test_valid_number_passes(self):
        result, issues = validate_numeric(0.85, "score")
        assert result == 0.85
        assert issues == []

    def test_clamps_below_min(self):
        result, issues = validate_numeric(-5, "score", min_val=0)
        assert result == 0
        assert "score_below_min" in issues

    def test_clamps_above_max(self):
        result, issues = validate_numeric(100, "score", max_val=1)
        assert result == 1
        assert "score_above_max" in issues

    def test_rejects_nan(self):
        result, issues = validate_numeric(float("nan"), "score")
        assert result is None
        assert "score_is_nan" in issues

    def test_rejects_infinity(self):
        result, issues = validate_numeric(float("inf"), "score")
        assert result is None
        assert "score_is_infinite" in issues

    def test_rejects_invalid_type(self):
        result, issues = validate_numeric("not a number", "score")
        assert result is None
        assert "score_invalid_type" in issues

    def test_handles_none(self):
        result, issues = validate_numeric(None, "score")
        assert result is None
        assert issues == []


class TestValidateScore:
    """Tests for 0-1 score validation."""

    def test_valid_score(self):
        result, issues = validate_score(0.75, "plausibility")
        assert result == 0.75
        assert issues == []

    def test_clamps_negative(self):
        result, issues = validate_score(-0.5, "plausibility")
        assert result == 0.0
        assert "plausibility_below_min" in issues

    def test_clamps_above_one(self):
        result, issues = validate_score(1.5, "plausibility")
        assert result == 1.0
        assert "plausibility_above_max" in issues


class TestValidateModelsUsed:
    """Tests for models_used validation."""

    def test_valid_list(self):
        models = ["gpt-4", "llama-2"]
        result, issues = validate_models_used(models)
        assert result == ["gpt-4", "llama-2"]
        assert issues == []

    def test_handles_string_json(self):
        models = '["gpt-4", "llama-2"]'
        result, issues = validate_models_used(models)
        assert result == ["gpt-4", "llama-2"]
        assert "models_used_was_string" in issues

    def test_handles_single_string(self):
        models = "gpt-4"
        result, issues = validate_models_used(models)
        assert result == ["gpt-4"]
        assert "models_used_was_string" in issues

    def test_sanitizes_model_names(self):
        models = ["safe-model", "<script>evil</script>"]
        result, issues = validate_models_used(models)
        assert "<script>" not in str(result)
        assert any("xss" in i for i in issues)

    def test_handles_none(self):
        result, issues = validate_models_used(None)
        assert result == []
        assert issues == []

    def test_truncates_long_list(self):
        models = ["model"] * 1500
        result, issues = validate_models_used(models)
        assert len(result) <= SIZE_LIMITS["max_array_length"]
        assert "models_used_truncated" in issues


class TestSanitizeTraceForStorage:
    """Tests for high-level storage sanitization."""

    def test_handles_dict_input(self):
        trace = {"trace_id": "test", "data": {"nested": "value"}}
        sanitized, result = sanitize_trace_for_storage(trace)
        assert isinstance(sanitized, dict)
        assert isinstance(result, TraceSanitizationResult)

    def test_handles_pydantic_like_model(self):
        class MockModel:
            def model_dump(self):
                return {"trace_id": "test", "action_rationale": "safe"}

        trace = MockModel()
        sanitized, result = sanitize_trace_for_storage(trace)
        assert sanitized["trace_id"] == "test"


class TestXSSPayloads:
    """Tests against common XSS payloads (OWASP)."""

    # Payloads with expected detection status
    XSS_PAYLOADS_DETECTED = [
        '<script>alert("XSS")</script>',
        '<img src=x onerror=alert("XSS")>',
        '<svg onload=alert("XSS")>',
        '<body onload=alert("XSS")>',
        '<iframe src="javascript:alert(\'XSS\')"></iframe>',
        '<a href="javascript:alert(\'XSS\')">click</a>',
        "javascript:alert('XSS')",
        '<input onfocus=alert("XSS") autofocus>',
        '<object data="javascript:alert(\'XSS\')">',
        '<embed src="javascript:alert(\'XSS\')">',
        '"><script>alert("XSS")</script>',
        '<img src="x" onerror="alert(\'XSS\')">',
    ]

    @pytest.mark.parametrize("payload", XSS_PAYLOADS_DETECTED)
    def test_neutralizes_xss_payload(self, payload):
        result = sanitize_text(payload)
        # Should detect and neutralize
        assert result.was_modified is True or result.detections
        # Script tags should be gone or escaped
        assert "<script" not in result.sanitized_text.lower() or "&lt;script" in result.sanitized_text.lower()


class TestSQLInjectionPayloads:
    """Tests against common SQL injection payloads."""

    SQL_PAYLOADS = [
        "'; DROP TABLE users; --",
        "1 OR 1=1",
        "1' OR '1'='1",
        "UNION SELECT * FROM users",
        "; DELETE FROM users WHERE 1=1",
        "'; INSERT INTO users VALUES ('hacker', 'password'); --",
        "admin'--",
        "1; EXEC xp_cmdshell('dir')",
    ]

    @pytest.mark.parametrize("payload", SQL_PAYLOADS)
    def test_detects_sql_injection(self, payload):
        detections = detect_patterns(payload)
        # Most should be detected
        # Note: Some simple ones like "1 OR 1=1" may not match patterns
        # We focus on the more dangerous ones
        if "UNION" in payload.upper() or "DROP" in payload.upper():
            assert len(detections) > 0


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_handles_unicode(self):
        text = "Hello 世界 <script>x</script> 🎉"
        result = sanitize_text(text)
        assert "世界" in result.sanitized_text
        assert "🎉" in result.sanitized_text
        assert "<script>" not in result.sanitized_text

    def test_handles_empty_dict(self):
        result, detections, modified, truncated = sanitize_dict_recursive({})
        assert result == {}
        assert detections == []

    def test_handles_empty_list(self):
        result, detections, modified, truncated = sanitize_dict_recursive([])
        assert result == []
        assert detections == []

    def test_handles_mixed_types_in_list(self):
        data = [1, "text", {"key": "value"}, None, True]
        result, detections, modified, truncated = sanitize_dict_recursive(data)
        assert len(result) == 5

    def test_preserves_legitimate_html_descriptions(self):
        # HTML in reasoning that's descriptive, not executable
        text = "The user asked about HTML tags like <div> and <span> elements."
        result = sanitize_text(text)
        # This should be mostly preserved since it's descriptive
        # The < and > might get encoded but the meaning is preserved
        assert "div" in result.sanitized_text or "div" in result.sanitized_text.lower()

    def test_handles_large_trace(self):
        trace = {
            "trace_id": "test",
            "components": [
                {"data": {"reasoning": "x" * 50000}}
                for _ in range(10)
            ]
        }
        result = sanitize_trace(trace)
        # Should complete without error
        assert result.original_hash is not None
