//! Pipeline context management.
//!
//! Provides batch and trace context for logging and state tracking.

use chrono::{DateTime, Utc};
use uuid::Uuid;

/// Context for a batch of traces.
#[derive(Debug, Clone)]
pub struct BatchContext {
    pub batch_id: String,
    pub batch_timestamp: DateTime<Utc>,
    pub consent_timestamp: Option<DateTime<Utc>>,
    pub trace_level: String,
    pub correlation_metadata: Option<String>,
}

impl BatchContext {
    pub fn new(
        batch_timestamp: &str,
        consent_timestamp: Option<&str>,
        trace_level: &str,
        correlation_metadata: Option<&str>,
    ) -> Self {
        let batch_id = format!("batch-{}", &Uuid::new_v4().to_string()[..8]);

        let batch_ts = DateTime::parse_from_rfc3339(batch_timestamp)
            .map(|dt| dt.with_timezone(&Utc))
            .unwrap_or_else(|_| Utc::now());

        let consent_ts = consent_timestamp.and_then(|ts| {
            DateTime::parse_from_rfc3339(ts)
                .map(|dt| dt.with_timezone(&Utc))
                .ok()
        });

        Self {
            batch_id,
            batch_timestamp: batch_ts,
            consent_timestamp: consent_ts,
            trace_level: trace_level.to_string(),
            correlation_metadata: correlation_metadata.map(|s| s.to_string()),
        }
    }

    /// Create a trace context for this batch.
    pub fn trace_context(&self, trace_id: &str) -> TraceContext {
        TraceContext {
            batch_id: self.batch_id.clone(),
            trace_id: trace_id.to_string(),
            trace_level: self.trace_level.clone(),
        }
    }
}

/// Context for a single trace within a batch.
#[derive(Debug, Clone)]
pub struct TraceContext {
    pub batch_id: String,
    pub trace_id: String,
    pub trace_level: String,
}

impl TraceContext {
    pub fn log_context(&self) -> crate::logging::structured::LogContext {
        crate::logging::structured::LogContext::new(&self.batch_id).with_trace(&self.trace_id)
    }
}
