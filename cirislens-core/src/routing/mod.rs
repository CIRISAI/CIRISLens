//! Routing decision module.
//!
//! Determines where traces should be stored:
//! - Production table (accord_traces)
//! - Mock table (accord_traces_mock)
//! - Connectivity events table
//! - Malformed traces table

pub mod decision;
pub mod mock_detection;

pub use decision::*;
pub use mock_detection::*;
