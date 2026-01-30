//! Schema validation module.
//!
//! Provides DB-driven schema validation for trace ingestion:
//! - Schema detection based on event_types
//! - Schema caching with in-memory storage
//! - Signature verification for Ed25519 signatures

pub mod schema;
pub mod schema_cache;
pub mod signature;

pub use schema::*;
// schema_cache re-exports from schema module
pub use signature::*;
