//! Structured logging with trace context.
//!
//! Provides logging macros and utilities that include batch_id and trace_id
//! in every log message for easy correlation.

pub mod structured;

pub use structured::*;
