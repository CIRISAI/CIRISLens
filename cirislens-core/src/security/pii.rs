//! PII scrubbing for full_traces level.
//!
//! Detects and replaces personally identifiable information:
//! - Email addresses
//! - Phone numbers
//! - IP addresses
//! - URLs
//! - SSNs
//! - Credit card numbers

use lazy_static::lazy_static;
use regex::Regex;
use serde_json::Value;

use crate::logging::structured::LogContext;

lazy_static! {
    /// Email pattern
    static ref EMAIL_PATTERN: Regex = Regex::new(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    ).unwrap();

    /// Phone number patterns (various formats)
    static ref PHONE_PATTERN: Regex = Regex::new(
        r"(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}"
    ).unwrap();

    /// IP address pattern (IPv4)
    static ref IP_PATTERN: Regex = Regex::new(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    ).unwrap();

    /// URL pattern
    static ref URL_PATTERN: Regex = Regex::new(
        r"https?://[^\s<>]+"
    ).unwrap();

    /// SSN pattern
    static ref SSN_PATTERN: Regex = Regex::new(
        r"\b\d{3}-\d{2}-\d{4}\b"
    ).unwrap();

    /// Credit card pattern (basic)
    static ref CC_PATTERN: Regex = Regex::new(
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    ).unwrap();
}

/// Fields that should be scrubbed for PII in full_traces.
pub const PII_TARGET_FIELDS: &[&str] = &[
    "task_description",
    "initial_context",
    "system_snapshot",
    "gathered_context",
    "relevant_memories",
    "conversation_history",
    "reasoning",
    "prompt_used",
    "combined_analysis",
    "action_rationale",
    "reasoning_summary",
    "action_parameters",
    "aspdma_prompt",
    "conscience_override_reason",
    "epistemic_data",
    "updated_status_content",
    "entropy_reason",
    "coherence_reason",
    "optimization_veto_justification",
    "epistemic_humility_justification",
    "execution_error",
];

/// PII scrubbing result.
#[derive(Debug, Default)]
pub struct PiiScrubResult {
    pub emails_found: usize,
    pub phones_found: usize,
    pub ips_found: usize,
    pub urls_found: usize,
    pub ssns_found: usize,
    pub ccs_found: usize,
    pub fields_modified: usize,
}

impl PiiScrubResult {
    pub fn total_entities(&self) -> usize {
        self.emails_found
            + self.phones_found
            + self.ips_found
            + self.urls_found
            + self.ssns_found
            + self.ccs_found
    }
}

/// Scrub PII from a trace (for full_traces level only).
///
/// Replaces PII with placeholder tokens like [EMAIL], [PHONE], etc.
pub fn scrub_pii(trace: &Value, ctx: &LogContext) -> (Value, PiiScrubResult) {
    log::debug!("{} PII_SCRUB_START", ctx);

    let mut result = PiiScrubResult::default();
    let scrubbed = scrub_value(trace, ctx, &mut result);

    if result.total_entities() > 0 {
        log::info!(
            "{} PII_SCRUBBED emails={} phones={} ips={} urls={} ssns={} ccs={} fields_modified={}",
            ctx,
            result.emails_found,
            result.phones_found,
            result.ips_found,
            result.urls_found,
            result.ssns_found,
            result.ccs_found,
            result.fields_modified
        );
    } else {
        log::debug!("{} PII_SCRUB_COMPLETE entities_found=0", ctx);
    }

    (scrubbed, result)
}

/// Recursively scrub PII from a JSON value.
fn scrub_value(value: &Value, ctx: &LogContext, result: &mut PiiScrubResult) -> Value {
    match value {
        Value::String(s) => {
            let scrubbed = scrub_string(s, result);
            Value::String(scrubbed)
        }
        Value::Array(arr) => {
            let scrubbed: Vec<Value> = arr.iter().map(|v| scrub_value(v, ctx, result)).collect();
            Value::Array(scrubbed)
        }
        Value::Object(obj) => {
            let mut scrubbed = serde_json::Map::new();
            for (key, val) in obj {
                // Only scrub fields in the target list
                if PII_TARGET_FIELDS.contains(&key.as_str()) {
                    let original = val.to_string();
                    let scrubbed_val = scrub_value(val, ctx, result);
                    if scrubbed_val.to_string() != original {
                        result.fields_modified += 1;
                    }
                    scrubbed.insert(key.clone(), scrubbed_val);
                } else {
                    // Recursively check nested objects
                    scrubbed.insert(key.clone(), scrub_value(val, ctx, result));
                }
            }
            Value::Object(scrubbed)
        }
        _ => value.clone(),
    }
}

/// Scrub PII from a string.
fn scrub_string(s: &str, result: &mut PiiScrubResult) -> String {
    let mut scrubbed = s.to_string();

    // Email
    let email_count = EMAIL_PATTERN.find_iter(&scrubbed).count();
    if email_count > 0 {
        result.emails_found += email_count;
        scrubbed = EMAIL_PATTERN.replace_all(&scrubbed, "[EMAIL]").to_string();
    }

    // Phone
    let phone_count = PHONE_PATTERN.find_iter(&scrubbed).count();
    if phone_count > 0 {
        result.phones_found += phone_count;
        scrubbed = PHONE_PATTERN.replace_all(&scrubbed, "[PHONE]").to_string();
    }

    // IP addresses
    let ip_count = IP_PATTERN.find_iter(&scrubbed).count();
    if ip_count > 0 {
        result.ips_found += ip_count;
        scrubbed = IP_PATTERN.replace_all(&scrubbed, "[IP_ADDRESS]").to_string();
    }

    // URLs
    let url_count = URL_PATTERN.find_iter(&scrubbed).count();
    if url_count > 0 {
        result.urls_found += url_count;
        scrubbed = URL_PATTERN.replace_all(&scrubbed, "[URL]").to_string();
    }

    // SSN
    let ssn_count = SSN_PATTERN.find_iter(&scrubbed).count();
    if ssn_count > 0 {
        result.ssns_found += ssn_count;
        scrubbed = SSN_PATTERN.replace_all(&scrubbed, "[SSN]").to_string();
    }

    // Credit card
    let cc_count = CC_PATTERN.find_iter(&scrubbed).count();
    if cc_count > 0 {
        result.ccs_found += cc_count;
        scrubbed = CC_PATTERN.replace_all(&scrubbed, "[CREDIT_CARD]").to_string();
    }

    scrubbed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_email_scrubbing() {
        let mut result = PiiScrubResult::default();
        let scrubbed = scrub_string("Contact john@example.com for help", &mut result);
        assert_eq!(scrubbed, "Contact [EMAIL] for help");
        assert_eq!(result.emails_found, 1);
    }

    #[test]
    fn test_phone_scrubbing() {
        let mut result = PiiScrubResult::default();
        let scrubbed = scrub_string("Call 555-123-4567 now", &mut result);
        assert_eq!(scrubbed, "Call [PHONE] now");
        assert_eq!(result.phones_found, 1);
    }

    #[test]
    fn test_ip_scrubbing() {
        let mut result = PiiScrubResult::default();
        let scrubbed = scrub_string("Server at 192.168.1.100", &mut result);
        assert_eq!(scrubbed, "Server at [IP_ADDRESS]");
        assert_eq!(result.ips_found, 1);
    }

    #[test]
    fn test_no_pii() {
        let mut result = PiiScrubResult::default();
        let original = "This is a normal text without PII";
        let scrubbed = scrub_string(original, &mut result);
        assert_eq!(scrubbed, original);
        assert_eq!(result.total_entities(), 0);
    }

    #[test]
    fn test_trace_scrubbing() {
        let ctx = LogContext::new("test-batch");
        let trace = serde_json::json!({
            "thought_id": "test-123",
            "task_description": "Contact john@example.com about the issue",
            "other_field": "user@domain.com"  // Not in target fields, won't be scrubbed
        });

        let (scrubbed, result) = scrub_pii(&trace, &ctx);

        assert!(scrubbed["task_description"]
            .as_str()
            .unwrap()
            .contains("[EMAIL]"));
        assert!(result.emails_found > 0);
    }
}
