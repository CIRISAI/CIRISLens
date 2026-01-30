//! Routing decision logic.
//!
//! Determines the destination table for each trace.

use std::collections::HashMap;

use crate::logging::structured::LogContext;
use crate::routing::mock_detection::is_mock_trace;

/// Routing decision for a trace.
#[derive(Debug, Clone, PartialEq)]
pub enum RoutingDecision {
    Production,
    Mock,
    Connectivity,
    Malformed(String), // reason
}

impl RoutingDecision {
    pub fn as_str(&self) -> &str {
        match self {
            RoutingDecision::Production => "production",
            RoutingDecision::Mock => "mock",
            RoutingDecision::Connectivity => "connectivity",
            RoutingDecision::Malformed(_) => "malformed",
        }
    }
}

/// Determine routing for a trace based on extracted metadata.
///
/// # Decision Tree
/// 1. If schema_version == "connectivity" -> Connectivity
/// 2. If models_used contains "mock" -> Mock (unless generic level)
/// 3. Otherwise -> Production
pub fn determine_routing(
    metadata: &HashMap<String, String>,
    trace_level: &str,
    ctx: &LogContext,
) -> RoutingDecision {
    // Check for connectivity events
    if let Some(schema) = metadata.get("schema_version") {
        if schema == "connectivity" {
            log::debug!(
                "{} ROUTING_DECISION destination=connectivity reason=schema_version",
                ctx
            );
            return RoutingDecision::Connectivity;
        }
    }

    // Check for mock traces (skip for generic level)
    if trace_level != "generic" {
        let models_used = metadata
            .get("models_used")
            .map(|s| s.as_str())
            .unwrap_or("[]");

        if is_mock_trace(models_used) {
            log::info!(
                "{} ROUTING_DECISION destination=mock models_used={}",
                ctx,
                models_used
            );
            return RoutingDecision::Mock;
        }
    }

    // Default to production
    log::debug!("{} ROUTING_DECISION destination=production", ctx);
    RoutingDecision::Production
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_production_routing() {
        let ctx = LogContext::new("test-batch");
        let metadata: HashMap<String, String> = HashMap::new();

        let decision = determine_routing(&metadata, "detailed", &ctx);
        assert_eq!(decision, RoutingDecision::Production);
    }

    #[test]
    fn test_mock_routing() {
        let ctx = LogContext::new("test-batch");
        let mut metadata = HashMap::new();
        metadata.insert("models_used".to_string(), r#"["llama4scout (mock)"]"#.to_string());

        let decision = determine_routing(&metadata, "detailed", &ctx);
        assert_eq!(decision, RoutingDecision::Mock);
    }

    #[test]
    fn test_mock_routing_generic_level() {
        let ctx = LogContext::new("test-batch");
        let mut metadata = HashMap::new();
        metadata.insert("models_used".to_string(), r#"["mock-model"]"#.to_string());

        // Generic level should go to production even with mock models
        let decision = determine_routing(&metadata, "generic", &ctx);
        assert_eq!(decision, RoutingDecision::Production);
    }

    #[test]
    fn test_connectivity_routing() {
        let ctx = LogContext::new("test-batch");
        let mut metadata = HashMap::new();
        metadata.insert("schema_version".to_string(), "connectivity".to_string());

        let decision = determine_routing(&metadata, "detailed", &ctx);
        assert_eq!(decision, RoutingDecision::Connectivity);
    }
}
