//! SQL query builders.
//!
//! Generates SQL queries for trace storage.
//! Actual execution is handled by Python (asyncpg).

/// Get the list of columns for accord_traces table.
///
/// Returns tuples of (column_name, parameter_placeholder).
pub fn get_trace_columns() -> Vec<(&'static str, &'static str)> {
    vec![
        ("trace_id", "$1"),
        ("timestamp", "$2"),
        ("trace_level", "$3"),
        ("schema_version", "$4"),
        ("batch_timestamp", "$5"),
        ("consent_timestamp", "$6"),
        // Signature
        ("signature", "$7"),
        ("signature_key_id", "$8"),
        ("signature_verified", "$9"),
        // PII
        ("pii_scrubbed", "$10"),
        ("original_content_hash", "$11"),
        // THOUGHT_START fields
        ("thought_id", "$12"),
        ("thought_type", "$13"),
        ("thought_depth", "$14"),
        ("task_id", "$15"),
        ("task_description", "$16"),
        ("started_at", "$17"),
        // SNAPSHOT_AND_CONTEXT fields
        ("agent_name", "$18"),
        ("cognitive_state", "$19"),
        // DMA fields
        ("csdma_plausibility", "$20"),
        ("csdma_confidence", "$21"),
        ("dsdma_alignment", "$22"),
        ("dsdma_confidence", "$23"),
        ("pdma_stakeholder_score", "$24"),
        ("pdma_conflict_detected", "$25"),
        // IDMA fields
        ("idma_k_eff", "$26"),
        ("idma_correlation_risk", "$27"),
        ("idma_fragility_flag", "$28"),
        ("idma_phase", "$29"),
        ("idma_confidence", "$30"),
        // ASPDMA fields
        ("selected_action", "$31"),
        ("action_rationale", "$32"),
        ("aspdma_confidence", "$33"),
        // TSASPDMA fields
        ("tool_name", "$34"),
        ("tool_parameters", "$35"),
        ("tsaspdma_reasoning", "$36"),
        ("tsaspdma_approved", "$37"),
        // CONSCIENCE fields
        ("conscience_passed", "$38"),
        ("conscience_override", "$39"),
        ("conscience_override_reason", "$40"),
        ("epistemic_humility", "$41"),
        ("entropy_awareness", "$42"),
        ("coherence_alignment", "$43"),
        // ACTION_RESULT fields
        ("action_success", "$44"),
        ("action_type", "$45"),
        ("tokens_used", "$46"),
        ("cost_usd", "$47"),
        ("completed_at", "$48"),
        ("positive_moment", "$49"),
        ("models_used", "$50"),
        ("api_bases_used", "$51"),
        // Full JSON blobs
        ("dma_results", "$52"),
        ("aspdma_result", "$53"),
        ("idma_result", "$54"),
        ("tsaspdma_result", "$55"),
        ("conscience_result", "$56"),
        ("action_result", "$57"),
        ("initial_context", "$58"),
        ("system_snapshot", "$59"),
        ("gathered_context", "$60"),
    ]
}

/// Build INSERT query for accord_traces.
pub fn build_trace_insert() -> String {
    let columns = get_trace_columns();
    let col_names: Vec<&str> = columns.iter().map(|(name, _)| *name).collect();
    let placeholders: Vec<&str> = columns.iter().map(|(_, ph)| *ph).collect();

    format!(
        "INSERT INTO cirislens.accord_traces ({}) VALUES ({}) ON CONFLICT (trace_id) DO NOTHING",
        col_names.join(", "),
        placeholders.join(", ")
    )
}

/// Build INSERT query for connectivity_events.
pub fn build_connectivity_insert() -> &'static str {
    r#"
    INSERT INTO cirislens.connectivity_events
        (trace_id, timestamp, event_type, agent_id, agent_name, agent_id_hash,
         event_data, signature, signature_key_id, signature_verified,
         consent_timestamp, trace_level)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
    "#
}

/// Build INSERT query for malformed_traces.
pub fn build_malformed_insert() -> &'static str {
    r#"
    INSERT INTO cirislens.malformed_traces
        (trace_id, content_hash, rejection_reason, event_types, trace_level, received_at)
    VALUES ($1, $2, $3, $4, $5, $6)
    "#
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_trace_insert_query() {
        let query = build_trace_insert();
        assert!(query.contains("INSERT INTO cirislens.accord_traces"));
        assert!(query.contains("trace_id"));
        assert!(query.contains("ON CONFLICT"));
    }

    #[test]
    fn test_connectivity_insert_query() {
        let query = build_connectivity_insert();
        assert!(query.contains("INSERT INTO cirislens.connectivity_events"));
        assert!(query.contains("event_type"));
    }

    #[test]
    fn test_column_count() {
        let columns = get_trace_columns();
        // Should have 60 columns
        assert_eq!(columns.len(), 60);
    }
}
