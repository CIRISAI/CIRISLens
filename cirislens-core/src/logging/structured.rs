//! Structured logging utilities.
//!
//! Provides context-aware logging with batch_id and trace_id included
//! in every log message.

use std::fmt;

/// Logging context for a batch of traces.
#[derive(Debug, Clone)]
pub struct LogContext {
    pub batch_id: String,
    pub trace_id: Option<String>,
}

impl LogContext {
    pub fn new(batch_id: &str) -> Self {
        Self {
            batch_id: batch_id.to_string(),
            trace_id: None,
        }
    }

    pub fn with_trace(&self, trace_id: &str) -> Self {
        Self {
            batch_id: self.batch_id.clone(),
            trace_id: Some(trace_id.to_string()),
        }
    }
}

impl fmt::Display for LogContext {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.trace_id {
            Some(tid) => write!(f, "[batch={}] [trace={}]", self.batch_id, tid),
            None => write!(f, "[batch={}]", self.batch_id),
        }
    }
}

/// Log an info message with context.
#[macro_export]
macro_rules! log_info {
    ($ctx:expr, $event:expr, $($key:ident = $value:expr),* $(,)?) => {
        log::info!(
            "{} {} {}",
            $ctx,
            $event,
            format_args!($(concat!(stringify!($key), "={:?} "), $value),*)
        );
    };
}

/// Log a warning message with context.
#[macro_export]
macro_rules! log_warn {
    ($ctx:expr, $event:expr, $($key:ident = $value:expr),* $(,)?) => {
        log::warn!(
            "{} {} {}",
            $ctx,
            $event,
            format_args!($(concat!(stringify!($key), "={:?} "), $value),*)
        );
    };
}

/// Log an error message with context.
#[macro_export]
macro_rules! log_error {
    ($ctx:expr, $event:expr, $($key:ident = $value:expr),* $(,)?) => {
        log::error!(
            "{} {} {}",
            $ctx,
            $event,
            format_args!($(concat!(stringify!($key), "={:?} "), $value),*)
        );
    };
}

/// Log a debug message with context.
#[macro_export]
macro_rules! log_debug {
    ($ctx:expr, $event:expr, $($key:ident = $value:expr),* $(,)?) => {
        log::debug!(
            "{} {} {}",
            $ctx,
            $event,
            format_args!($(concat!(stringify!($key), "={:?} "), $value),*)
        );
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_log_context_display() {
        let ctx = LogContext::new("batch-123");
        assert_eq!(format!("{}", ctx), "[batch=batch-123]");

        let ctx_with_trace = ctx.with_trace("trace-456");
        assert_eq!(
            format!("{}", ctx_with_trace),
            "[batch=batch-123] [trace=trace-456]"
        );
    }
}
