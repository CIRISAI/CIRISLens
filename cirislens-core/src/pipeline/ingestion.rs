//! Main trace ingestion pipeline.
//!
//! Coordinates the full trace processing workflow:
//! 1. Schema validation (DB-driven)
//! 2. Connectivity event handling
//! 3. Signature verification
//! 4. PII scrubbing (full_traces level)
//! 5. Security sanitization
//! 6. Metadata extraction (dynamic from schema)
//! 7. Mock detection & routing
//! 8. Return routing decisions and extracted metadata

use std::collections::{HashMap, HashSet};

use serde_json::Value;

use crate::extraction::metadata::extract_trace_metadata;
use crate::logging::structured::LogContext;
use crate::routing::decision::{determine_routing, RoutingDecision};
use crate::security::pii::scrub_pii;
use crate::security::sanitizer::sanitize_trace;
use crate::validation::schema::{get_schema_cache, SchemaValidationResult};
use crate::validation::signature::verify_signature;

use super::context::BatchContext;

/// Result of processing a single trace.
#[derive(Debug)]
pub struct TraceResult {
    pub trace_id: String,
    pub destination: String, // production, mock, connectivity, malformed
    pub schema_version: Option<String>,
    pub accepted: bool,
    pub rejection_reason: Option<String>,
    pub extracted_metadata: HashMap<String, String>,
}

/// Result of processing a batch.
#[derive(Debug)]
pub struct BatchResult {
    pub received_count: usize,
    pub accepted_count: usize,
    pub rejected_count: usize,
    pub traces: Vec<TraceResult>,
}

/// Process a batch of traces.
///
/// Main entry point for trace processing.
pub fn process_batch(ctx: &BatchContext, events: Vec<String>) -> BatchResult {
    let mut results = Vec::new();
    let mut accepted = 0;
    let mut rejected = 0;

    for event_json in &events {
        let result = process_single_trace(ctx, event_json);

        if result.accepted {
            accepted += 1;
        } else {
            rejected += 1;
        }

        results.push(result);
    }

    log::info!(
        "[batch={}] BATCH_COMPLETE received={} accepted={} rejected={}",
        ctx.batch_id,
        events.len(),
        accepted,
        rejected
    );

    BatchResult {
        received_count: events.len(),
        accepted_count: accepted,
        rejected_count: rejected,
        traces: results,
    }
}

/// Process a single trace.
fn process_single_trace(batch_ctx: &BatchContext, event_json: &str) -> TraceResult {
    // Parse JSON
    let trace: Value = match serde_json::from_str(event_json) {
        Ok(v) => v,
        Err(e) => {
            log::warn!(
                "[batch={}] TRACE_PARSE_FAILED error={}",
                batch_ctx.batch_id,
                e
            );
            return TraceResult {
                trace_id: "unknown".to_string(),
                destination: "malformed".to_string(),
                schema_version: None,
                accepted: false,
                rejection_reason: Some(format!("JSON parse error: {}", e)),
                extracted_metadata: HashMap::new(),
            };
        }
    };

    // Extract trace_id
    let trace_id = trace
        .get("trace_id")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    let trace_ctx = batch_ctx.trace_context(&trace_id);
    let log_ctx = trace_ctx.log_context();

    log::debug!("{} TRACE_PROCESS_START", log_ctx);

    // [1] SCHEMA VALIDATION
    let schema_result = validate_schema(&trace, &log_ctx);

    if !schema_result.valid {
        log::warn!(
            "{} SCHEMA_INVALID reason={:?}",
            log_ctx,
            schema_result.reason
        );
        return TraceResult {
            trace_id,
            destination: "malformed".to_string(),
            schema_version: None,
            accepted: false,
            rejection_reason: schema_result.reason,
            extracted_metadata: HashMap::new(),
        };
    }

    let schema_version = schema_result.version.unwrap_or_default();

    // [2] CONNECTIVITY EVENT HANDLING
    if schema_version == "connectivity" {
        log::info!(
            "{} CONNECTIVITY_EVENT schema_version={}",
            log_ctx,
            schema_version
        );
        return TraceResult {
            trace_id,
            destination: "connectivity".to_string(),
            schema_version: Some(schema_version),
            accepted: true,
            rejection_reason: None,
            extracted_metadata: extract_connectivity_metadata(&trace),
        };
    }

    // [3] SIGNATURE VERIFICATION
    // Signatures are REQUIRED for trace integrity - no bypass
    let signature_result = verify_trace_signature(&trace, &trace_ctx.trace_level, &log_ctx);

    if !signature_result.verified {
        log::warn!(
            "{} SIGNATURE_REJECTED key_id={:?} reason={:?}",
            log_ctx,
            signature_result.key_id,
            signature_result.error
        );
        return TraceResult {
            trace_id,
            destination: "malformed".to_string(),
            schema_version: Some(schema_version),
            accepted: false,
            rejection_reason: signature_result.error,
            extracted_metadata: HashMap::new(),
        };
    }

    // [4] PII SCRUBBING (full_traces level only)
    let trace_to_process = if trace_ctx.trace_level == "full_traces" {
        log::info!("{} PII_SCRUB_START level=full_traces", log_ctx);
        let (scrubbed, pii_result) = scrub_pii(&trace, &log_ctx);
        if pii_result.total_entities() > 0 {
            log::info!(
                "{} PII_SCRUBBED total_entities={} fields_modified={}",
                log_ctx,
                pii_result.total_entities(),
                pii_result.fields_modified
            );
        }
        scrubbed
    } else {
        log::debug!("{} PII_SKIPPED level={}", log_ctx, trace_ctx.trace_level);
        trace.clone()
    };

    // [5] SECURITY SANITIZATION
    let sanitized_trace = sanitize_trace(&trace_to_process, &log_ctx);

    // [6] METADATA EXTRACTION
    let mut extracted_metadata = extract_trace_metadata(&sanitized_trace, &schema_version, &log_ctx);

    // Add signature verification result to metadata
    extracted_metadata.insert(
        "signature_verified".to_string(),
        signature_result.verified.to_string(),
    );
    if let Some(ref key_id) = signature_result.key_id {
        extracted_metadata.insert(
            "signature_key_id".to_string(),
            key_id.clone(),
        );
    }

    // [7] MOCK DETECTION & ROUTING
    let routing = determine_routing(&extracted_metadata, &trace_ctx.trace_level, &log_ctx);

    let destination = match routing {
        RoutingDecision::Production => "production",
        RoutingDecision::Mock => "mock",
        RoutingDecision::Connectivity => "connectivity",
        RoutingDecision::Malformed(_) => "malformed",
    };

    log::info!(
        "{} TRACE_COMPLETE destination={} schema_version={}",
        log_ctx,
        destination,
        schema_version
    );

    TraceResult {
        trace_id,
        destination: destination.to_string(),
        schema_version: Some(schema_version),
        accepted: true,
        rejection_reason: None,
        extracted_metadata,
    }
}

/// Validate trace schema.
fn validate_schema(trace: &Value, ctx: &LogContext) -> SchemaValidationResult {
    // Extract event_types from components
    let event_types: HashSet<String> = trace
        .get("components")
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| c.get("event_type").and_then(|e| e.as_str()))
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();

    // Also check for single event_type field (connectivity events)
    let single_event_type = trace
        .get("event_type")
        .and_then(|e| e.as_str())
        .map(|s| s.to_string());

    let mut all_events = event_types;
    if let Some(evt) = single_event_type {
        all_events.insert(evt);
    }

    log::debug!("{} SCHEMA_CHECK events={:?}", ctx, all_events);

    if all_events.is_empty() {
        return SchemaValidationResult::invalid("No event_types found", all_events);
    }

    // Look up schema from cache
    let cache = get_schema_cache();

    if !cache.is_loaded() {
        log::warn!("{} SCHEMA_CACHE_NOT_LOADED", ctx);
        // Accept trace but flag as unknown version
        return SchemaValidationResult::valid("unknown", all_events);
    }

    match cache.detect_schema_version(&all_events, ctx) {
        Some(schema) => SchemaValidationResult::valid(&schema.version, all_events),
        None => SchemaValidationResult::invalid(
            &format!("No matching schema for events: {:?}", all_events),
            all_events,
        ),
    }
}

/// Verify trace signature.
///
/// Extracts signature and key_id from trace and verifies against loaded public keys.
///
/// Supports three formats:
/// - 1.9.9+: Wrapper object {"components": [...], "trace_level": "..."}, compact JSON, sorted keys
/// - 1.9.7+: Components array only, compact JSON with strip_empty
/// - Pre-1.9.7: Components array only, JSON with spaces, no stripping
fn verify_trace_signature(
    trace: &Value,
    batch_trace_level: &str,
    ctx: &LogContext,
) -> crate::validation::signature::SignatureVerificationResult {
    // Extract signature fields
    let signature = trace.get("signature").and_then(|v| v.as_str());
    let key_id = trace.get("signature_key_id").and_then(|v| v.as_str());

    match (signature, key_id) {
        (Some(sig), Some(kid)) => {
            // Get components array
            let components = match trace.get("components") {
                Some(c) => c,
                None => {
                    log::warn!("{} SIGNATURE_NO_COMPONENTS", ctx);
                    return crate::validation::signature::SignatureVerificationResult {
                        verified: false,
                        key_id: Some(kid.to_string()),
                        error: Some("No components array for signature verification".to_string()),
                    };
                }
            };

            // Use batch-level trace_level for 1.9.9 format (from API request, not trace object)
            let trace_level = batch_trace_level;

            // Try 1.9.9 format first: {"components": [...], "trace_level": "..."}
            // Compact JSON with sorted keys, no stripping
            let canonical_199 = build_199_canonical(components, trace_level);
            let hash_199 = crate::validation::signature::compute_hash(&canonical_199);
            let hash_199_short: String = hash_199.chars().take(16).collect();
            let preview_start: String = canonical_199.chars().take(300).collect();
            log::info!(
                "{} SIGNATURE_199_DEBUG key_id={} level={} len={} hash={} preview={}",
                ctx, kid, trace_level, canonical_199.len(), hash_199_short, preview_start
            );

            let result_199 = verify_signature(&canonical_199, sig, kid, ctx);
            if result_199.verified {
                log::info!(
                    "{} SIGNATURE_VERIFIED format=1.9.9 key_id={} len={} hash={}",
                    ctx, kid, canonical_199.len(), hash_199_short
                );
                return result_199;
            }

            // Try 1.9.7 format (compact + strip_empty, components only)
            let canonical_197 = sort_and_serialize(components);
            let hash_197 = crate::validation::signature::compute_hash(&canonical_197);
            log::debug!(
                "{} SIGNATURE_TRY_FORMAT format=1.9.7 key_id={} len={} hash={}",
                ctx, kid, canonical_197.len(), hash_197
            );

            let result_197 = verify_signature(&canonical_197, sig, kid, ctx);
            if result_197.verified {
                log::info!(
                    "{} SIGNATURE_VERIFIED format=1.9.7 key_id={} len={} hash={}",
                    ctx, kid, canonical_197.len(), hash_197
                );
                return result_197;
            }

            // Try pre-1.9.7 format (with spaces, no stripping, components only)
            let canonical_pre197 = sort_and_serialize_legacy(components);
            let hash_pre197 = crate::validation::signature::compute_hash(&canonical_pre197);
            log::debug!(
                "{} SIGNATURE_TRY_FORMAT format=pre-1.9.7 key_id={} len={} hash={}",
                ctx, kid, canonical_pre197.len(), hash_pre197
            );

            let result_pre197 = verify_signature(&canonical_pre197, sig, kid, ctx);
            if result_pre197.verified {
                log::info!(
                    "{} SIGNATURE_VERIFIED format=pre-1.9.7 key_id={} len={} hash={}",
                    ctx, kid, canonical_pre197.len(), hash_pre197
                );
                return result_pre197;
            }

            // All formats failed - log details for troubleshooting
            let preview_199: String = canonical_199.chars().take(200).collect();
            log::warn!(
                "{} SIGNATURE_VERIFICATION_FAILED key_id={} tried_formats=[1.9.9,1.9.7,pre-1.9.7] \
                 hash_199={} hash_197={} hash_pre197={} preview_199={}...",
                ctx, kid, hash_199_short, hash_197, hash_pre197, preview_199
            );

            // Return the 1.9.9 result (most recent format)
            result_199
        }
        (None, _) => {
            log::debug!("{} SIGNATURE_MISSING", ctx);
            crate::validation::signature::SignatureVerificationResult::no_signature()
        }
        (Some(_), None) => {
            log::warn!("{} SIGNATURE_KEY_ID_MISSING", ctx);
            crate::validation::signature::SignatureVerificationResult {
                verified: false,
                key_id: None,
                error: Some("Signature present but key_id missing".to_string()),
            }
        }
    }
}

/// Check if a value is "empty" (null, empty string, empty array, empty object).
fn is_empty_value(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::String(s) => s.is_empty(),
        Value::Array(arr) => arr.is_empty(),
        Value::Object(map) => map.is_empty(),
        _ => false,
    }
}

/// Recursively strip empty values from a JSON value.
fn strip_empty(value: &Value) -> Option<Value> {
    match value {
        Value::Object(map) => {
            let filtered: serde_json::Map<String, Value> = map
                .iter()
                .filter_map(|(k, v)| {
                    if is_empty_value(v) {
                        None
                    } else {
                        strip_empty(v).map(|stripped| (k.clone(), stripped))
                    }
                })
                .collect();
            if filtered.is_empty() {
                None
            } else {
                Some(Value::Object(filtered))
            }
        }
        Value::Array(arr) => {
            let filtered: Vec<Value> = arr
                .iter()
                .filter_map(|v| {
                    if is_empty_value(v) {
                        None
                    } else {
                        strip_empty(v)
                    }
                })
                .collect();
            if filtered.is_empty() {
                None
            } else {
                Some(Value::Array(filtered))
            }
        }
        _ => Some(value.clone()),
    }
}

/// Serialize JSON value with sorted keys (recursive).
/// Uses compact JSON (no spaces) and strips empty values to match agent's _strip_empty().
fn sort_and_serialize(value: &Value) -> String {
    // First strip empty values
    let stripped = strip_empty(value).unwrap_or(Value::Null);
    sort_and_serialize_inner(&stripped)
}

/// Inner serialization function (after stripping).
fn sort_and_serialize_inner(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            // Sort keys and recursively process values
            let mut sorted: Vec<_> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));

            let pairs: Vec<String> = sorted
                .iter()
                .map(|(k, v)| format!("\"{}\":{}", k, sort_and_serialize_inner(v)))
                .collect();

            format!("{{{}}}", pairs.join(","))
        }
        Value::Array(arr) => {
            let items: Vec<String> = arr.iter().map(sort_and_serialize_inner).collect();
            format!("[{}]", items.join(","))
        }
        Value::String(s) => {
            // Properly escape the string for JSON
            serde_json::to_string(s).unwrap_or_else(|_| format!("\"{}\"", s))
        }
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".to_string(),
    }
}

/// Serialize JSON value with sorted keys for pre-1.9.7 format.
/// Uses spaces after `:` and `,` and does NOT strip empty values.
/// This matches Python's default: json.dumps(obj, sort_keys=True)
fn sort_and_serialize_legacy(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            // Sort keys and recursively process values
            let mut sorted: Vec<_> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));

            let pairs: Vec<String> = sorted
                .iter()
                .map(|(k, v)| format!("\"{}\": {}", k, sort_and_serialize_legacy(v)))
                .collect();

            format!("{{{}}}", pairs.join(", "))
        }
        Value::Array(arr) => {
            let items: Vec<String> = arr.iter().map(sort_and_serialize_legacy).collect();
            format!("[{}]", items.join(", "))
        }
        Value::String(s) => {
            // Properly escape the string for JSON
            serde_json::to_string(s).unwrap_or_else(|_| format!("\"{}\"", s))
        }
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".to_string(),
    }
}

/// Build 1.9.9 canonical message format.
/// Wrapper object: {"components": [...], "trace_level": "..."}
/// Compact JSON with sorted keys, NO stripping of empty values.
/// Matches Python: json.dumps(payload, sort_keys=True, separators=(",", ":"))
fn build_199_canonical(components: &Value, trace_level: &str) -> String {
    // Serialize components with sorted keys, compact format, no stripping
    let components_str = sort_and_serialize_compact(components);
    // Build wrapper object with sorted keys: "components" comes before "trace_level"
    format!("{{\"components\":{},\"trace_level\":\"{}\"}}", components_str, trace_level)
}

/// Serialize JSON value with sorted keys, compact format (no spaces).
/// Does NOT strip empty values - keeps nulls, empty strings, etc.
fn sort_and_serialize_compact(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut sorted: Vec<_> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));

            let pairs: Vec<String> = sorted
                .iter()
                .map(|(k, v)| format!("\"{}\":{}", k, sort_and_serialize_compact(v)))
                .collect();

            format!("{{{}}}", pairs.join(","))
        }
        Value::Array(arr) => {
            let items: Vec<String> = arr.iter().map(sort_and_serialize_compact).collect();
            format!("[{}]", items.join(","))
        }
        Value::String(s) => {
            serde_json::to_string(s).unwrap_or_else(|_| format!("\"{}\"", s))
        }
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".to_string(),
    }
}

/// Extract metadata from connectivity events.
fn extract_connectivity_metadata(trace: &Value) -> HashMap<String, String> {
    let mut metadata = HashMap::new();

    if let Some(event_type) = trace.get("event_type").and_then(|v| v.as_str()) {
        metadata.insert("event_type".to_string(), event_type.to_string());
    }

    if let Some(agent_name) = trace.get("agent_name").and_then(|v| v.as_str()) {
        metadata.insert("agent_name".to_string(), agent_name.to_string());
    }

    if let Some(agent_id) = trace.get("agent_id").and_then(|v| v.as_str()) {
        metadata.insert("agent_id".to_string(), agent_id.to_string());
    }

    if let Some(agent_id_hash) = trace.get("agent_id_hash").and_then(|v| v.as_str()) {
        metadata.insert("agent_id_hash".to_string(), agent_id_hash.to_string());
    }

    // Store full event data as JSON string
    metadata.insert("event_data".to_string(), trace.to_string());

    metadata
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_process_invalid_json() {
        let ctx = BatchContext::new(
            "2026-01-29T00:00:00Z",
            None,
            "detailed",
            None,
        );

        let result = process_single_trace(&ctx, "invalid json{");
        assert!(!result.accepted);
        assert_eq!(result.destination, "malformed");
        assert!(result.rejection_reason.is_some());
    }

    #[test]
    fn test_process_empty_events() {
        let ctx = BatchContext::new(
            "2026-01-29T00:00:00Z",
            None,
            "detailed",
            None,
        );

        let result = process_single_trace(&ctx, r#"{"trace_id": "test-123"}"#);
        // Without schema cache loaded, this should fail validation
        assert!(!result.accepted);
        assert_eq!(result.destination, "malformed");
    }
}
