//! Database models for trace storage.
//!
//! These models represent the structure of data in the database tables.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// Represents a trace ready for storage.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceRecord {
    pub trace_id: String,
    pub schema_version: Option<String>,
    pub trace_level: String,
    pub batch_timestamp: String,
    pub consent_timestamp: Option<String>,

    // Extracted metadata fields
    pub metadata: HashMap<String, String>,

    // Signature info
    pub signature: Option<String>,
    pub signature_key_id: Option<String>,
    pub signature_verified: bool,

    // PII scrubbing info
    pub pii_scrubbed: bool,
    pub original_content_hash: Option<String>,
}

/// Represents a connectivity event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConnectivityRecord {
    pub trace_id: String,
    pub event_type: String, // "startup" or "shutdown"
    pub agent_id: Option<String>,
    pub agent_name: Option<String>,
    pub agent_id_hash: Option<String>,
    pub event_data: String, // JSON
    pub signature: Option<String>,
    pub signature_key_id: Option<String>,
    pub signature_verified: bool,
    pub consent_timestamp: Option<String>,
    pub trace_level: String,
}

/// Represents a malformed trace.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MalformedRecord {
    pub trace_id: String,
    pub content_hash: String,
    pub rejection_reason: String,
    pub event_types: Vec<String>,
    pub trace_level: String,
    pub received_at: String,
}
