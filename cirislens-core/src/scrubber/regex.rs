//! Regex pass — structured-PII patterns and the historical-year residue check.

use lazy_static::lazy_static;
use regex::Regex;
use serde_json::Value;
use std::env;

use super::ScrubStats;

lazy_static! {
    pub(super) static ref EMAIL: Regex = Regex::new(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ).unwrap();

    pub(super) static ref PHONE: Regex = Regex::new(
        r"(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}"
    ).unwrap();

    pub(super) static ref IPV4: Regex = Regex::new(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    ).unwrap();

    pub(super) static ref URL: Regex = Regex::new(
        r"https?://[^\s<>]+"
    ).unwrap();

    pub(super) static ref SSN: Regex = Regex::new(
        r"\b\d{3}-\d{2}-\d{4}\b"
    ).unwrap();

    pub(super) static ref CREDIT_CARD: Regex = Regex::new(
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    ).unwrap();

    /// Historical years (1700-2023). Excludes 2024+ to preserve current
    /// timestamps in conversation. The cutoff bumps each year via release
    /// process — see FSD §10 for the operational note.
    pub(super) static ref HISTORICAL_YEAR: Regex = Regex::new(
        r"\b(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])\b"
    ).unwrap();

    /// Year-bearing programmatic identifiers — `foo_1989_bar`, etc. NER
    /// doesn't tokenize these as natural language; the year regex alone
    /// would only strip the year, leaving the topic-revealing flanking
    /// tokens. This pattern eats the whole compound identifier.
    ///
    /// Requires at least one non-year word/hyphen character flanking the
    /// year on at least one side (alternation below), so a bare year falls
    /// through to HISTORICAL_YEAR and produces `[YEAR]` not `[IDENTIFIER]`.
    pub(super) static ref YEAR_IDENTIFIER: Regex = Regex::new(
        r"\b(?:[\w\-]{1,40}(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])[\w\-]{0,40}|[\w\-]{0,40}(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])[\w\-]{1,40})\b"
    ).unwrap();
}

/// Apply all regex patterns to a string in the order: identifier → year →
/// structured PII. Identifier first because year is a substring of identifier.
pub(super) fn scrub_string(s: &str, stats: &mut ScrubStats) -> String {
    let mut out = s.to_string();

    let mut count = |pat: &Regex, replacement: &str, text: String| -> String {
        let n = pat.find_iter(&text).count();
        if n > 0 {
            stats.regex_redactions += n;
            pat.replace_all(&text, replacement).to_string()
        } else {
            text
        }
    };

    out = count(&YEAR_IDENTIFIER, "[IDENTIFIER]", out);
    out = count(&HISTORICAL_YEAR, "[YEAR]", out);
    out = count(&EMAIL, "[EMAIL]", out);
    out = count(&PHONE, "[PHONE]", out);
    out = count(&IPV4, "[IP_ADDRESS]", out);
    out = count(&URL, "[URL]", out);
    out = count(&SSN, "[SSN]", out);
    out = count(&CREDIT_CARD, "[CREDIT_CARD]", out);

    out
}

/// Count residual historical-year matches in scrubbed output. Any nonzero
/// count means the regex pass missed something — caller rejects the trace.
pub fn count_year_residue(value: &Value) -> usize {
    let mut total = 0usize;
    walk_strings(value, &mut |s| {
        total += HISTORICAL_YEAR.find_iter(s).count();
    });
    total
}

/// Check whether any operator-supplied probe term (CIRISLENS_LEAK_PROBES,
/// newline-separated) appears in the scrubbed output. Returns `true` if
/// any probe matched — caller rejects.
///
/// The probe list is read from the env at call time; intentionally not
/// cached so operators can update the list without restarting the service.
pub fn probe_match(value: &Value) -> bool {
    let probes_env = match env::var("CIRISLENS_LEAK_PROBES") {
        Ok(s) if !s.is_empty() => s,
        _ => return false,
    };
    let probes: Vec<String> = probes_env
        .split('\n')
        .filter(|p| !p.trim().is_empty())
        .map(|p| p.to_lowercase())
        .collect();

    let mut hit = false;
    walk_strings(value, &mut |s| {
        if hit {
            return;
        }
        let s_lower = s.to_lowercase();
        if probes.iter().any(|p| s_lower.contains(p)) {
            hit = true;
        }
    });
    hit
}

/// Visit every string leaf in a JSON value.
fn walk_strings<F: FnMut(&str)>(value: &Value, f: &mut F) {
    match value {
        Value::String(s) => f(s),
        Value::Array(arr) => arr.iter().for_each(|v| walk_strings(v, f)),
        Value::Object(obj) => obj.values().for_each(|v| walk_strings(v, f)),
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn historical_year_redacts() {
        let mut s = ScrubStats::default();
        let out = scrub_string("Event in 1989", &mut s);
        assert_eq!(out, "Event in [YEAR]");
    }

    #[test]
    fn current_year_preserved() {
        let mut s = ScrubStats::default();
        let out = scrub_string("Today is 2026-04-25", &mut s);
        assert!(out.contains("2026"));
    }

    #[test]
    fn year_identifier_eats_topic_tokens() {
        let mut s = ScrubStats::default();
        let out = scrub_string("source: foo_1989_bar", &mut s);
        // Whole compound identifier should collapse to placeholder.
        assert_eq!(out, "source: [IDENTIFIER]");
        assert!(!out.contains("foo"));
        assert!(!out.contains("bar"));
    }

    #[test]
    fn email_phone_ip() {
        let mut s = ScrubStats::default();
        let out = scrub_string("alice@example.com 555-123-4567 192.168.1.1", &mut s);
        assert!(out.contains("[EMAIL]"));
        assert!(out.contains("[PHONE]"));
        assert!(out.contains("[IP_ADDRESS]"));
        assert_eq!(s.regex_redactions, 3);
    }

    #[test]
    fn year_residue_check_finds_misses() {
        use serde_json::json;
        let v = json!({"escaped": "text with 1989 in it"});
        assert_eq!(count_year_residue(&v), 1);
    }
}
