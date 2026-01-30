//! CIRISLens Core - High-performance trace ingestion pipeline
//!
//! This crate provides the core trace processing functionality for CIRISLens,
//! exposed to Python via PyO3. The implementation prioritizes:
//!
//! 1. **Security** - Defense-in-depth with comprehensive validation
//! 2. **Logging** - Every decision point logged with full context
//! 3. **Performance** - Zero-copy where possible, parallel processing
//!
//! ## Architecture
//!
//! The crate is organized into modules:
//! - `pipeline` - Main ingestion orchestrator
//! - `validation` - Schema detection and validation (DB-driven)
//! - `security` - Sanitization, PII scrubbing, signature verification
//! - `extraction` - Dynamic field extraction from schema rules
//! - `routing` - Trace routing decisions (production/mock/malformed)
//! - `storage` - SQL query builders and models
//! - `logging` - Structured logging with trace context

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

pub mod extraction;
pub mod logging;
pub mod pipeline;
pub mod routing;
pub mod security;
pub mod storage;
pub mod validation;

use pipeline::context::BatchContext;
use pipeline::ingestion::process_batch;

/// Initialize the module-level logger
fn init_logger() {
    let _ = env_logger::builder()
        .filter_level(log::LevelFilter::Info)
        .format_timestamp_millis()
        .try_init();
}

/// Process a batch of traces.
///
/// This is the main entry point from Python. It handles:
/// - Schema validation (using cached DB schemas)
/// - Security sanitization
/// - Signature verification
/// - PII scrubbing (for full_traces level)
/// - Field extraction (dynamic from schema)
/// - Routing decisions
///
/// # Arguments
/// * `events` - List of trace events (JSON serialized)
/// * `batch_timestamp` - Timestamp for the batch
/// * `consent_timestamp` - When user consented to telemetry
/// * `trace_level` - "generic", "detailed", or "full_traces"
/// * `correlation_metadata` - Optional correlation data
///
/// # Returns
/// BatchResult with routing decisions and extracted metadata for each trace
#[pyfunction]
#[pyo3(signature = (events, batch_timestamp, consent_timestamp=None, trace_level="detailed".to_string(), correlation_metadata=None))]
fn process_trace_batch(
    py: Python<'_>,
    events: Vec<String>,
    batch_timestamp: String,
    consent_timestamp: Option<String>,
    trace_level: String,
    correlation_metadata: Option<String>,
) -> PyResult<Py<PyAny>> {
    init_logger();

    let ctx = BatchContext::new(
        &batch_timestamp,
        consent_timestamp.as_deref(),
        &trace_level,
        correlation_metadata.as_deref(),
    );

    log::info!(
        "BATCH_RECEIVED batch_id={} traces={} level={}",
        ctx.batch_id,
        events.len(),
        trace_level
    );

    let result = process_batch(&ctx, events);

    // Convert to Python dict
    let py_result = PyDict::new(py);
    py_result.set_item("batch_id", &ctx.batch_id)?;
    py_result.set_item("received_count", result.received_count)?;
    py_result.set_item("accepted_count", result.accepted_count)?;
    py_result.set_item("rejected_count", result.rejected_count)?;

    // Convert trace results to Python list of dicts
    let traces_list = PyList::empty(py);
    for trace in result.traces {
        let trace_dict = PyDict::new(py);
        trace_dict.set_item("trace_id", &trace.trace_id)?;
        trace_dict.set_item("destination", &trace.destination)?;
        trace_dict.set_item("schema_version", &trace.schema_version)?;
        trace_dict.set_item("accepted", trace.accepted)?;

        if let Some(reason) = &trace.rejection_reason {
            trace_dict.set_item("rejection_reason", reason)?;
        }

        // Convert extracted metadata to Python dict
        let metadata_dict = PyDict::new(py);
        for (key, value) in &trace.extracted_metadata {
            metadata_dict.set_item(key, value)?;
        }
        trace_dict.set_item("extracted_metadata", metadata_dict)?;

        traces_list.append(trace_dict)?;
    }
    py_result.set_item("traces", traces_list)?;

    Ok(py_result.into())
}

/// Load schemas from database into cache.
///
/// Called at startup to populate the schema cache.
/// Schemas are stored as JSON strings in the database.
///
/// # Arguments
/// * `schemas` - List of schema rows from trace_schemas table
/// * `fields` - List of field rows from trace_schema_fields table
#[pyfunction]
fn load_schemas_from_db(
    schemas: Vec<(String, String, String, Vec<String>)>, // (version, description, status, signature_events)
    fields: Vec<(String, String, String, String, String, bool, String)>, // (schema_ver, event_type, field_name, json_path, data_type, required, db_column)
) -> PyResult<()> {
    init_logger();

    let mut cache = validation::schema::get_schema_cache_mut();
    cache.load_from_db_rows(schemas, fields);

    log::info!(
        "SCHEMA_CACHE_LOADED_FROM_DB schemas={:?}",
        cache.schema_versions()
    );

    Ok(())
}

/// Refresh the schema cache.
///
/// Call this after modifying schemas in the database.
#[pyfunction]
fn refresh_schema_cache() -> PyResult<()> {
    init_logger();
    validation::schema::get_schema_cache_mut().clear();
    log::info!("SCHEMA_CACHE_CLEARED");
    Ok(())
}

/// Get the currently loaded schema versions.
#[pyfunction]
fn get_loaded_schemas() -> PyResult<Vec<String>> {
    let cache = validation::schema::get_schema_cache();
    Ok(cache.schema_versions())
}

/// Load public keys from database into cache.
///
/// # Arguments
/// * `keys` - List of (key_id, public_key_base64) tuples
#[pyfunction]
fn load_public_keys_from_db(keys: Vec<(String, String)>) -> PyResult<()> {
    init_logger();

    let mut cache = validation::signature::get_key_cache_mut();
    cache.clear();

    let mut loaded = 0;
    let mut errors = Vec::new();

    for (key_id, public_key_base64) in keys {
        match cache.load_key(&key_id, &public_key_base64) {
            Ok(()) => loaded += 1,
            Err(e) => errors.push(format!("{}: {}", key_id, e)),
        }
    }

    cache.mark_loaded();

    log::info!(
        "PUBLIC_KEY_CACHE_LOADED keys={} errors={}",
        loaded,
        errors.len()
    );

    if !errors.is_empty() {
        log::warn!("PUBLIC_KEY_LOAD_ERRORS: {:?}", errors);
    }

    Ok(())
}

/// Refresh the public key cache.
#[pyfunction]
fn refresh_public_key_cache() -> PyResult<()> {
    init_logger();
    validation::signature::get_key_cache_mut().clear();
    Ok(())
}

/// Get count of loaded public keys.
#[pyfunction]
fn get_public_key_count() -> PyResult<usize> {
    let cache = validation::signature::get_key_cache();
    Ok(cache.key_count())
}

/// Check if caches need refresh (TTL expired).
///
/// Returns (schema_needs_refresh, keys_need_refresh)
#[pyfunction]
fn check_cache_status() -> PyResult<(bool, bool, Option<u64>, Option<u64>)> {
    let schema_cache = validation::schema::get_schema_cache();
    let key_cache = validation::signature::get_key_cache();

    Ok((
        schema_cache.needs_refresh(),
        key_cache.needs_refresh(),
        schema_cache.cache_age_secs(),
        key_cache.cache_age_secs(),
    ))
}

/// Python module definition
#[pymodule]
fn cirislens_core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process_trace_batch, m)?)?;
    m.add_function(wrap_pyfunction!(load_schemas_from_db, m)?)?;
    m.add_function(wrap_pyfunction!(refresh_schema_cache, m)?)?;
    m.add_function(wrap_pyfunction!(get_loaded_schemas, m)?)?;
    m.add_function(wrap_pyfunction!(load_public_keys_from_db, m)?)?;
    m.add_function(wrap_pyfunction!(refresh_public_key_cache, m)?)?;
    m.add_function(wrap_pyfunction!(get_public_key_count, m)?)?;
    m.add_function(wrap_pyfunction!(check_cache_status, m)?)?;
    Ok(())
}
