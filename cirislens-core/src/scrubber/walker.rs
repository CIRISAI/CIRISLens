//! JSON subtree walker — fixes the v1 walker bug where lists of strings
//! under a SCRUB_FIELDS-keyed parent escaped scrubbing because the list
//! elements had no key to match against.
//!
//! The new contract: when a key in SCRUB_FIELDS is encountered, every
//! string in that subtree is scrubbed, regardless of nesting.

use serde_json::{Map, Value};
use std::collections::HashSet;

use super::regex::scrub_string;
use super::{ScrubError, ScrubStats};

const MAX_DEPTH: usize = 30;

/// Walk and scrub a trace's JSON. When a key in `scrub_fields` matches,
/// every string in that subtree is passed through `scrub_string` (and
/// optionally the NER pass when `run_ner` is true).
pub fn walk(
    value: Value,
    scrub_fields: &HashSet<&'static str>,
    stats: &mut ScrubStats,
    run_ner: bool,
) -> Result<Value, ScrubError> {
    walk_inner(value, scrub_fields, stats, run_ner, /* in_scope = */ false, 0)
}

fn walk_inner(
    value: Value,
    scrub_fields: &HashSet<&'static str>,
    stats: &mut ScrubStats,
    run_ner: bool,
    in_scope: bool,
    depth: usize,
) -> Result<Value, ScrubError> {
    if depth > MAX_DEPTH {
        return Err(ScrubError::WalkerDepthExceeded(depth));
    }
    if depth > stats.walker_max_depth {
        stats.walker_max_depth = depth;
    }

    match value {
        Value::String(s) => {
            if in_scope {
                let scrubbed = scrub_text(&s, run_ner, stats)?;
                if scrubbed != s {
                    stats.fields_modified += 1;
                }
                Ok(Value::String(scrubbed))
            } else {
                Ok(Value::String(s))
            }
        }

        Value::Array(arr) => {
            let mut out = Vec::with_capacity(arr.len());
            for item in arr {
                out.push(walk_inner(item, scrub_fields, stats, run_ner, in_scope, depth + 1)?);
            }
            Ok(Value::Array(out))
        }

        Value::Object(obj) => {
            let mut out = Map::with_capacity(obj.len());
            for (key, val) in obj {
                let child_in_scope = in_scope || scrub_fields.contains(key.as_str());
                let scrubbed_val = walk_inner(
                    val,
                    scrub_fields,
                    stats,
                    run_ner,
                    child_in_scope,
                    depth + 1,
                )?;
                out.insert(key, scrubbed_val);
            }
            Ok(Value::Object(out))
        }

        other => Ok(other),
    }
}

/// Apply scrubbing passes to a single string.
fn scrub_text(s: &str, run_ner: bool, stats: &mut ScrubStats) -> Result<String, ScrubError> {
    // Fast path: empty / whitespace-only strings need no work.
    if s.trim().is_empty() {
        return Ok(s.to_string());
    }

    let mut out = s.to_string();

    // NER pass first (if enabled) — replaces named entities with [<TAG>_<n>]
    // placeholders. Regex pass then catches structured PII not picked up by
    // NER and any remaining historical years.
    if run_ner {
        out = super::ner::scrub_with_ner(&out, stats)?;
    }
    out = scrub_string(&out, stats);

    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fields() -> HashSet<&'static str> {
        let mut s = HashSet::new();
        s.insert("flags");
        s.insert("source_ids");
        s.insert("task_description");
        s
    }

    #[test]
    fn list_of_strings_under_matched_key_scrubbed() {
        // The v1 bug: list elements escape because they have no key to
        // match. New walker scrubs every string in a matched subtree.
        let trace = json!({
            "dma_results": {
                "csdma": {
                    "flags": ["Event in 1989", "user_query_1989_topic"]
                }
            }
        });
        let mut stats = ScrubStats::default();
        let out = walk(trace, &fields(), &mut stats, false).unwrap();
        let flags = out["dma_results"]["csdma"]["flags"].as_array().unwrap();
        assert_eq!(flags[0].as_str().unwrap(), "Event in [YEAR]");
        assert_eq!(flags[1].as_str().unwrap(), "[IDENTIFIER]");
    }

    #[test]
    fn unmatched_subtree_unchanged() {
        let trace = json!({
            "metadata": {
                "non_scrub_field": "Year 1989 stays here"
            }
        });
        let mut stats = ScrubStats::default();
        let out = walk(trace.clone(), &fields(), &mut stats, false).unwrap();
        // Field is not in SCRUB_FIELDS, year stays.
        assert!(out["metadata"]["non_scrub_field"]
            .as_str()
            .unwrap()
            .contains("1989"));
    }

    #[test]
    fn nested_dict_under_matched_key_scrubbed() {
        let trace = json!({
            "task_description": {
                "primary": "see 1989 event",
                "alt": ["also 1989"]
            }
        });
        let mut stats = ScrubStats::default();
        let out = walk(trace, &fields(), &mut stats, false).unwrap();
        assert!(!out.to_string().contains("1989"));
    }

    #[test]
    fn depth_limit_enforced() {
        // Build pathological deep nesting.
        let mut v = Value::String("payload".to_string());
        for _ in 0..40 {
            v = json!({"x": v});
        }
        let mut stats = ScrubStats::default();
        let result = walk(v, &fields(), &mut stats, false);
        assert!(matches!(result, Err(ScrubError::WalkerDepthExceeded(_))));
    }

    #[test]
    fn scrub_stats_tracks_modifications() {
        let trace = json!({
            "task_description": "Event in 1989"
        });
        let mut stats = ScrubStats::default();
        walk(trace, &fields(), &mut stats, false).unwrap();
        assert_eq!(stats.fields_modified, 1);
        assert_eq!(stats.regex_redactions, 1);
    }
}
