"""
Hypothesis strategies for CIRISLens property-based testing.

These strategies generate test data for various domain objects.
"""

from hypothesis import strategies as st

# =============================================================================
# Token Strategies
# =============================================================================

# Valid service tokens (format: svc_<32 hex chars>)
service_tokens = st.from_regex(r"svc_[a-f0-9]{32}", fullmatch=True)

# Invalid tokens (various malformed formats)
invalid_tokens = st.one_of(
    st.just(""),
    st.just("invalid"),
    st.text(max_size=10),
    st.from_regex(r"svc_[a-f0-9]{10}", fullmatch=True),  # Too short
    st.from_regex(r"SVC_[a-f0-9]{32}", fullmatch=True),  # Wrong case
)

# =============================================================================
# Log Strategies
# =============================================================================

# Log levels
log_levels = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

# Invalid log levels
invalid_log_levels = st.one_of(
    st.just("TRACE"),
    st.just("FATAL"),
    st.just("info"),  # Wrong case
    st.text(min_size=1, max_size=20).filter(
        lambda x: x.upper() not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    ),
)

# ISO timestamps with timezone
timestamps = st.datetimes().map(lambda dt: dt.isoformat() + "Z")

# Service names (lowercase, 3-20 chars)
service_names = st.from_regex(r"[a-z][a-z0-9_]{2,19}", fullmatch=True)

# Log messages (non-empty strings, reasonable length)
log_messages = st.text(min_size=1, max_size=2000, alphabet=st.characters(
    blacklist_categories=("Cs",),  # Exclude surrogates
))

# Complete log entries
log_entries = st.fixed_dictionaries({
    "timestamp": timestamps,
    "level": log_levels,
    "message": log_messages,
    "service_name": service_names,
}).map(lambda d: {**d, "event": "test_event"})

# Batch of log entries
log_batches = st.lists(log_entries, min_size=1, max_size=100)

# =============================================================================
# PII Test Data (for sanitization testing)
# =============================================================================

# Email addresses
emails = st.emails()

# Credit card-like numbers (for testing redaction)
credit_cards = st.from_regex(
    r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
    fullmatch=True
)

# SSN-like numbers
ssns = st.from_regex(r"\d{3}-\d{2}-\d{4}", fullmatch=True)

# Bearer tokens
bearer_tokens = st.from_regex(r"Bearer [A-Za-z0-9\-_\.]{20,100}", fullmatch=True)

# Messages with PII embedded
messages_with_pii = st.one_of(
    emails.map(lambda e: f"User email is {e}"),
    credit_cards.map(lambda c: f"Card number: {c}"),
    ssns.map(lambda s: f"SSN: {s}"),
    bearer_tokens.map(lambda t: f"Auth: {t}"),
)

# =============================================================================
# Status Strategies
# =============================================================================

# Status values
status_values = st.sampled_from(["operational", "degraded", "outage", "unknown"])

# Region codes
regions = st.sampled_from(["us", "eu", "global"])

# Provider names
provider_names = st.sampled_from([
    "postgresql", "grafana", "openrouter", "groq", "together", "openai",
    "stripe", "service"
])

# Latency values (milliseconds)
latency_ms = st.integers(min_value=1, max_value=30000)

# Status check records
status_checks = st.fixed_dictionaries({
    "service_name": st.sampled_from(["cirislens", "cirisbilling", "cirisproxy"]),
    "provider_name": provider_names,
    "region": regions,
    "status": status_values,
    "latency_ms": st.one_of(latency_ms, st.none()),
    "error_message": st.one_of(st.text(max_size=200), st.none()),
})

# =============================================================================
# OTLP Strategies
# =============================================================================

# Trace IDs (32 hex chars)
trace_ids = st.from_regex(r"[a-f0-9]{32}", fullmatch=True)

# Span IDs (16 hex chars)
span_ids = st.from_regex(r"[a-f0-9]{16}", fullmatch=True)

# Span names
span_names = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_\.]{2,50}", fullmatch=True)

# Metric names
metric_names = st.from_regex(r"[a-z][a-z0-9_\.]{2,50}", fullmatch=True)

# Metric values
metric_values = st.one_of(
    st.integers(min_value=0, max_value=10**9),
    st.floats(min_value=0, max_value=10**6, allow_nan=False, allow_infinity=False),
)

# =============================================================================
# API Request Strategies
# =============================================================================

# Valid days parameter for history endpoint
valid_days = st.integers(min_value=1, max_value=365)

# Invalid days parameter
invalid_days = st.one_of(
    st.integers(max_value=0),
    st.integers(min_value=366),
)

# Pagination parameters
page_params = st.fixed_dictionaries({
    "page": st.integers(min_value=1, max_value=1000),
    "per_page": st.integers(min_value=1, max_value=100),
})

# =============================================================================
# Composite Strategies
# =============================================================================


def status_response_for_region(region: str):
    """Generate a status response for a specific region."""
    return st.fixed_dictionaries({
        "region": st.just(region),
        "status": status_values,
        "services": st.dictionaries(
            keys=st.sampled_from(["billing", "proxy", "manager"]),
            values=st.fixed_dictionaries({
                "status": status_values,
                "latency_ms": latency_ms,
            }),
            min_size=1,
            max_size=3,
        ),
    })


# Full aggregated status response
aggregated_status = st.fixed_dictionaries({
    "overall_status": status_values,
    "timestamp": timestamps,
    "regions": st.fixed_dictionaries({
        "us": status_response_for_region("us"),
        "eu": status_response_for_region("eu"),
    }),
})
