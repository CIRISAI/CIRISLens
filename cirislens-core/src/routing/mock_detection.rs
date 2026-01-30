//! Mock trace detection.
//!
//! Detects traces from test/mock LLM models to route them to the mock table.

/// Check if a models_used string indicates a mock trace.
///
/// Returns true if any model name contains "mock" (case-insensitive).
///
/// # Examples
/// ```
/// assert!(is_mock_trace(r#"["llama4scout (mock)"]"#));
/// assert!(is_mock_trace(r#"["mock-model"]"#));
/// assert!(!is_mock_trace(r#"["claude-3-sonnet"]"#));
/// ```
pub fn is_mock_trace(models_used: &str) -> bool {
    models_used.to_lowercase().contains("mock")
}

/// Extract model names from a JSON array string.
pub fn parse_models_used(models_json: &str) -> Vec<String> {
    // Parse JSON array
    serde_json::from_str::<Vec<String>>(models_json).unwrap_or_default()
}

/// Check if any model in the list is a mock model.
pub fn contains_mock_model(models: &[String]) -> bool {
    models
        .iter()
        .any(|m| m.to_lowercase().contains("mock"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_mock_trace() {
        assert!(is_mock_trace(r#"["llama4scout (mock)"]"#));
        assert!(is_mock_trace(r#"["Mock-Model"]"#));
        assert!(is_mock_trace(r#"["model1", "mock-test"]"#));
        assert!(!is_mock_trace(r#"["claude-3-sonnet"]"#));
        assert!(!is_mock_trace(r#"["gpt-4"]"#));
        assert!(!is_mock_trace(r#"[]"#));
    }

    #[test]
    fn test_parse_models_used() {
        let models = parse_models_used(r#"["claude-3", "gpt-4"]"#);
        assert_eq!(models, vec!["claude-3", "gpt-4"]);

        let empty = parse_models_used(r#"[]"#);
        assert!(empty.is_empty());

        let invalid = parse_models_used("invalid");
        assert!(invalid.is_empty());
    }

    #[test]
    fn test_contains_mock_model() {
        let with_mock = vec!["claude-3".to_string(), "mock-test".to_string()];
        assert!(contains_mock_model(&with_mock));

        let without_mock = vec!["claude-3".to_string(), "gpt-4".to_string()];
        assert!(!contains_mock_model(&without_mock));
    }
}
