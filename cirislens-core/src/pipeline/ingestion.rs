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
use crate::security::sanitizer::sanitize_trace;
use crate::validation::schema::{get_schema_cache, SchemaValidationResult};

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
    // TODO: Implement signature verification
    // For now, we skip this step and accept all traces

    // [4] PII SCRUBBING (full_traces level only)
    // TODO: Implement PII scrubbing
    // For now, we skip this step

    // [5] SECURITY SANITIZATION
    let sanitized_trace = sanitize_trace(&trace, &log_ctx);

    // [6] METADATA EXTRACTION
    let extracted_metadata = extract_trace_metadata(&sanitized_trace, &schema_version, &log_ctx);

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
