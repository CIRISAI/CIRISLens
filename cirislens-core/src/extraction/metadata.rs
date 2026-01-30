//! Metadata extraction from traces.
//!
//! Dynamic field extraction based on schema definitions from database.
//! Uses JSON path resolution to extract values and convert to target types.

use std::collections::HashMap;

use serde_json::Value;

use crate::extraction::json_path::{resolve_json_path, value_to_bool, value_to_float, value_to_int, value_to_string};
use crate::logging::structured::LogContext;
use crate::validation::schema::get_schema_cache;

/// Extract metadata from a trace using schema-defined field rules.
///
/// # Arguments
/// * `trace` - The trace JSON
/// * `schema_version` - The detected schema version
/// * `ctx` - Logging context
///
/// # Returns
/// HashMap of db_column -> value (as strings for simplicity)
pub fn extract_trace_metadata(
    trace: &Value,
    schema_version: &str,
    ctx: &LogContext,
) -> HashMap<String, String> {
    let mut metadata = HashMap::new();

    log::debug!(
        "{} EXTRACT_START schema_version={}",
        ctx,
        schema_version
    );

    // Get components from trace
    let components = trace
        .get("components")
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();

    // Get schema cache
    let cache = get_schema_cache();

    if !cache.is_loaded() {
        log::warn!("{} EXTRACT_SKIP reason=schema_cache_not_loaded", ctx);
        return metadata;
    }

    // Extract trace-level fields
    if let Some(trace_id) = trace.get("trace_id").and_then(|v| v.as_str()) {
        metadata.insert("trace_id".to_string(), trace_id.to_string());
    }

    // Process each component
    for component in &components {
        let event_type = component
            .get("event_type")
            .and_then(|e| e.as_str())
            .unwrap_or("unknown");

        let data = component.get("data").unwrap_or(component);

        // Get field rules for this schema/event_type
        let field_rules = cache.get_field_rules(schema_version, event_type);

        log::debug!(
            "{} EXTRACT_COMPONENT event_type={} rules_count={}",
            ctx,
            event_type,
            field_rules.len()
        );

        // Extract each field
        for rule in field_rules {
            let value = resolve_json_path(data, &rule.json_path);

            match value {
                Some(v) => {
                    let extracted = convert_value(v, &rule.data_type);
                    metadata.insert(rule.db_column.clone(), extracted.clone());

                    log::debug!(
                        "{} FIELD_EXTRACTED field={} path={} db_col={} value={:?}",
                        ctx,
                        rule.field_name,
                        rule.json_path,
                        rule.db_column,
                        extracted
                    );
                }
                None => {
                    if rule.required {
                        log::warn!(
                            "{} FIELD_MISSING field={} event_type={} required=true",
                            ctx,
                            rule.field_name,
                            event_type
                        );
                    }
                }
            }
        }

        // Also store the full component data as JSON for certain event types
        store_full_component(&mut metadata, event_type, data);
    }

    log::debug!(
        "{} EXTRACT_COMPLETE fields_populated={}",
        ctx,
        metadata.len()
    );

    metadata
}

/// Convert a JSON value to a string based on target data type.
fn convert_value(value: &Value, data_type: &str) -> String {
    match data_type {
        "float" => value_to_float(value)
            .map(|f| f.to_string())
            .unwrap_or_default(),
        "int" => value_to_int(value)
            .map(|i| i.to_string())
            .unwrap_or_default(),
        "boolean" => value_to_bool(value)
            .map(|b| b.to_string())
            .unwrap_or_default(),
        "json" => value.to_string(),
        "timestamp" => value_to_string(value),
        _ => value_to_string(value), // string and default
    }
}

/// Store full component data for certain event types.
fn store_full_component(metadata: &mut HashMap<String, String>, event_type: &str, data: &Value) {
    let key = match event_type {
        "DMA_RESULTS" => Some("dma_results"),
        "ASPDMA_RESULT" => Some("aspdma_result"),
        "IDMA_RESULT" => Some("idma_result"),
        "TSASPDMA_RESULT" => Some("tsaspdma_result"),
        "CONSCIENCE_RESULT" => Some("conscience_result"),
        "ACTION_RESULT" => Some("action_result"),
        _ => None,
    };

    if let Some(key) = key {
        // Only store if not already present (specific extraction takes precedence)
        if !metadata.contains_key(key) {
            metadata.insert(key.to_string(), data.to_string());
        }
    }
}

/// Extract models_used from trace (for mock detection).
pub fn extract_models_used(trace: &Value) -> Vec<String> {
    // Look in components
    trace
        .get("components")
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    c.get("data")
                        .and_then(|d| d.get("models_used"))
                        .and_then(|m| m.as_array())
                        .map(|models| {
                            models
                                .iter()
                                .filter_map(|m| m.as_str().map(|s| s.to_string()))
                                .collect::<Vec<_>>()
                        })
                })
                .flatten()
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_extract_models_used() {
        let trace = json!({
            "components": [
                {
                    "event_type": "ACTION_RESULT",
                    "data": {
                        "models_used": ["claude-3", "gpt-4"]
                    }
                }
            ]
        });

        let models = extract_models_used(&trace);
        assert_eq!(models, vec!["claude-3", "gpt-4"]);
    }

    #[test]
    fn test_extract_models_used_empty() {
        let trace = json!({
            "components": []
        });

        let models = extract_models_used(&trace);
        assert!(models.is_empty());
    }

    #[test]
    fn test_convert_value() {
        assert_eq!(convert_value(&json!(1.5), "float"), "1.5");
        assert_eq!(convert_value(&json!(42), "int"), "42");
        assert_eq!(convert_value(&json!(true), "boolean"), "true");
        assert_eq!(convert_value(&json!("test"), "string"), "test");
    }
}
