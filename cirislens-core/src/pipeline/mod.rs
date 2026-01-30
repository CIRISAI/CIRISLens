//! Pipeline orchestration module.
//!
//! Main trace ingestion pipeline that coordinates:
//! - Schema validation
//! - Security sanitization
//! - Signature verification
//! - PII scrubbing
//! - Field extraction
//! - Routing decisions

pub mod context;
pub mod ingestion;

pub use context::*;
pub use ingestion::*;
