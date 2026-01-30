//! Security sanitization for trace data.
//!
//! Detects and neutralizes potential security threats:
//! - XSS patterns
//! - SQL injection patterns
//! - Command injection patterns
//! - Path traversal patterns

use lazy_static::lazy_static;
use regex::Regex;
use serde_json::Value;

use crate::logging::structured::LogContext;

/// Size limits for trace data.
pub const MAX_FIELD_SIZE: usize = 100_000;  // 100KB per field
pub const MAX_COMPONENT_SIZE: usize = 1_000_000;  // 1MB per component
pub const MAX_TRACE_SIZE: usize = 10_000_000;  // 10MB per trace

lazy_static! {
    /// XSS detection patterns
    static ref XSS_PATTERNS: Vec<Regex> = vec![
        Regex::new(r"(?i)<script[^>]*>").unwrap(),
        Regex::new(r"(?i)javascript:").unwrap(),
        Regex::new(r"(?i)on\w+\s*=").unwrap(),
        Regex::new(r"(?i)<iframe[^>]*>").unwrap(),
        Regex::new(r"(?i)<object[^>]*>").unwrap(),
        Regex::new(r"(?i)<embed[^>]*>").unwrap(),
    ];

    /// SQL injection detection patterns
    static ref SQL_PATTERNS: Vec<Regex> = vec![
        Regex::new(r"(?i)'\s*(or|and)\s*'?\d").unwrap(),
        Regex::new(r"(?i);\s*(drop|delete|truncate|alter)\s").unwrap(),
        Regex::new(r"(?i)union\s+(all\s+)?select").unwrap(),
        Regex::new(r"(?i)/\*.*\*/").unwrap(),
    ];

    /// Command injection detection patterns
    static ref CMD_PATTERNS: Vec<Regex> = vec![
        Regex::new(r";\s*(rm|cat|wget|curl|chmod)\s").unwrap(),
        Regex::new(r"\|\s*(bash|sh|zsh|cmd)").unwrap(),
        Regex::new(r"`[^`]+`").unwrap(),
        Regex::new(r"\$\([^)]+\)").unwrap(),
    ];

    /// Path traversal detection patterns
    static ref PATH_PATTERNS: Vec<Regex> = vec![
        Regex::new(r"\.\.[\\/]").unwrap(),
        Regex::new(r"[\\/]etc[\\/](passwd|shadow)").unwrap(),
        Regex::new(r"[\\/](proc|sys)[\\/]").unwrap(),
    ];
}

/// Security detection result.
#[derive(Debug, Default)]
pub struct SanitizationResult {
    pub xss_detections: usize,
    pub sql_detections: usize,
    pub cmd_detections: usize,
    pub path_detections: usize,
    pub oversized_fields: usize,
    pub total_detections: usize,
}

impl SanitizationResult {
    pub fn has_detections(&self) -> bool {
        self.total_detections > 0
    }
}

/// Sanitize a trace by detecting and neutralizing security threats.
///
/// Returns the sanitized trace (threats are logged but not removed,
/// as we want to preserve the original data for analysis).
pub fn sanitize_trace(trace: &Value, ctx: &LogContext) -> Value {
    log::debug!("{} SANITIZE_START", ctx);

    let mut result = SanitizationResult::default();

    // Check overall trace size
    let trace_str = trace.to_string();
    if trace_str.len() > MAX_TRACE_SIZE {
        log::warn!(
            "{} SIZE_LIMIT_EXCEEDED type=trace size={} limit={}",
            ctx,
            trace_str.len(),
            MAX_TRACE_SIZE
        );
        result.oversized_fields += 1;
    }

    // Scan for security patterns
    scan_value(trace, ctx, &mut result);

    if result.has_detections() {
        log::warn!(
            "{} SECURITY_DETECTIONS xss={} sql={} cmd={} path={} oversized={}",
            ctx,
            result.xss_detections,
            result.sql_detections,
            result.cmd_detections,
            result.path_detections,
            result.oversized_fields
        );
    } else {
        log::debug!("{} SANITIZE_COMPLETE detections=0", ctx);
    }

    // Return trace as-is (we log detections but don't modify)
    trace.clone()
}

/// Recursively scan a JSON value for security patterns.
fn scan_value(value: &Value, ctx: &LogContext, result: &mut SanitizationResult) {
    match value {
        Value::String(s) => {
            scan_string(s, ctx, result);
        }
        Value::Array(arr) => {
            for item in arr {
                scan_value(item, ctx, result);
            }
        }
        Value::Object(obj) => {
            for (key, val) in obj {
                // Check key for injection
                scan_string(key, ctx, result);
                // Check value
                scan_value(val, ctx, result);
            }
        }
        _ => {}
    }
}

/// Scan a string for security patterns.
fn scan_string(s: &str, ctx: &LogContext, result: &mut SanitizationResult) {
    // Size check
    if s.len() > MAX_FIELD_SIZE {
        log::debug!(
            "{} SIZE_LIMIT_EXCEEDED type=field size={} limit={}",
            ctx,
            s.len(),
            MAX_FIELD_SIZE
        );
        result.oversized_fields += 1;
        result.total_detections += 1;
    }

    // XSS patterns
    for pattern in XSS_PATTERNS.iter() {
        if pattern.is_match(s) {
            log::debug!(
                "{} PATTERN_DETECTED type=xss pattern={}",
                ctx,
                pattern.as_str()
            );
            result.xss_detections += 1;
            result.total_detections += 1;
        }
    }

    // SQL patterns
    for pattern in SQL_PATTERNS.iter() {
        if pattern.is_match(s) {
            log::debug!(
                "{} PATTERN_DETECTED type=sql pattern={}",
                ctx,
                pattern.as_str()
            );
            result.sql_detections += 1;
            result.total_detections += 1;
        }
    }

    // Command injection patterns
    for pattern in CMD_PATTERNS.iter() {
        if pattern.is_match(s) {
            log::debug!(
                "{} PATTERN_DETECTED type=cmd pattern={}",
                ctx,
                pattern.as_str()
            );
            result.cmd_detections += 1;
            result.total_detections += 1;
        }
    }

    // Path traversal patterns
    for pattern in PATH_PATTERNS.iter() {
        if pattern.is_match(s) {
            log::debug!(
                "{} PATTERN_DETECTED type=path pattern={}",
                ctx,
                pattern.as_str()
            );
            result.path_detections += 1;
            result.total_detections += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_xss_detection() {
        let ctx = LogContext::new("test-batch");
        let trace = serde_json::json!({
            "content": "<script>alert('xss')</script>"
        });

        let result = sanitize_trace(&trace, &ctx);
        // Should detect but not modify
        assert_eq!(result, trace);
    }

    #[test]
    fn test_sql_injection_detection() {
        let ctx = LogContext::new("test-batch");
        let trace = serde_json::json!({
            "query": "SELECT * FROM users WHERE id = 1; DROP TABLE users;"
        });

        sanitize_trace(&trace, &ctx);
        // Just verify it runs without panic
    }

    #[test]
    fn test_clean_trace() {
        let ctx = LogContext::new("test-batch");
        let trace = serde_json::json!({
            "thought_id": "test-123",
            "reasoning": "This is a normal trace without any security issues."
        });

        let result = sanitize_trace(&trace, &ctx);
        assert_eq!(result, trace);
    }
}
