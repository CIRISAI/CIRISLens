//! DB-driven schema validation.
//!
//! Provides schema detection and validation using schemas loaded from database.
//! No hardcoded schema definitions - everything comes from trace_schemas table.

use std::collections::{HashMap, HashSet};
use std::sync::RwLock;

use lazy_static::lazy_static;

use crate::logging::structured::LogContext;

/// Field extraction rule loaded from database.
#[derive(Debug, Clone)]
pub struct FieldExtractionRule {
    pub field_name: String,
    pub json_path: String,
    pub data_type: String, // string, float, int, boolean, json, timestamp
    pub required: bool,
    pub db_column: String,
}

/// Schema definition loaded from database.
#[derive(Debug, Clone)]
pub struct SchemaDefinition {
    pub version: String,
    pub description: String,
    pub status: String, // current, supported, deprecated
    pub signature_event_types: HashSet<String>,
    pub field_extractions: HashMap<String, Vec<FieldExtractionRule>>, // event_type -> rules
    pub match_mode: String, // "all" or "any"
    pub special_handling: bool,
}

impl SchemaDefinition {
    /// Check if this schema matches the given event types.
    pub fn matches(&self, event_types: &HashSet<String>) -> bool {
        if self.match_mode == "any" {
            // Any signature event present = match (for connectivity)
            !event_types.is_disjoint(&self.signature_event_types)
        } else {
            // All signature events must be present (superset)
            event_types.is_superset(&self.signature_event_types)
        }
    }
}

/// In-memory cache for trace schemas.
#[derive(Debug, Default)]
pub struct SchemaCache {
    schemas: HashMap<String, SchemaDefinition>,
    schemas_by_priority: Vec<SchemaDefinition>,
    loaded: bool,
}

impl SchemaCache {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn is_loaded(&self) -> bool {
        self.loaded
    }

    pub fn schema_versions(&self) -> Vec<String> {
        self.schemas.keys().cloned().collect()
    }

    pub fn get_schema(&self, version: &str) -> Option<&SchemaDefinition> {
        self.schemas.get(version)
    }

    pub fn schemas_by_priority(&self) -> &[SchemaDefinition] {
        &self.schemas_by_priority
    }

    /// Detect schema version from event types.
    pub fn detect_schema_version(
        &self,
        event_types: &HashSet<String>,
        ctx: &LogContext,
    ) -> Option<&SchemaDefinition> {
        log::debug!(
            "{} SCHEMA_CHECK events={:?}",
            ctx,
            event_types
        );

        for schema in &self.schemas_by_priority {
            if schema.matches(event_types) {
                log::info!(
                    "{} SCHEMA_MATCHED version={} status={} signature={:?}",
                    ctx,
                    schema.version,
                    schema.status,
                    schema.signature_event_types
                );
                return Some(schema);
            }
        }

        log::warn!(
            "{} SCHEMA_UNKNOWN events={:?} known_schemas={:?}",
            ctx,
            event_types,
            self.schema_versions()
        );
        None
    }

    /// Get field extraction rules for a schema/event_type.
    pub fn get_field_rules(&self, version: &str, event_type: &str) -> Vec<&FieldExtractionRule> {
        self.schemas
            .get(version)
            .and_then(|s| s.field_extractions.get(event_type))
            .map(|rules| rules.iter().collect())
            .unwrap_or_default()
    }

    /// Load schemas from database rows.
    ///
    /// # Arguments
    /// * `schemas` - (version, description, status, signature_events)
    /// * `fields` - (schema_ver, event_type, field_name, json_path, data_type, required, db_column)
    pub fn load_from_db_rows(
        &mut self,
        schemas: Vec<(String, String, String, Vec<String>)>,
        fields: Vec<(String, String, String, String, String, bool, String)>,
    ) {
        // Group fields by (schema_version, event_type)
        let mut fields_by_schema: HashMap<String, HashMap<String, Vec<FieldExtractionRule>>> =
            HashMap::new();

        for (schema_ver, event_type, field_name, json_path, data_type, required, db_column) in
            fields
        {
            let rule = FieldExtractionRule {
                field_name,
                json_path,
                data_type,
                required,
                db_column,
            };

            fields_by_schema
                .entry(schema_ver)
                .or_default()
                .entry(event_type)
                .or_default()
                .push(rule);
        }

        // Build schema definitions
        let mut defs = Vec::new();
        for (version, description, status, signature_events) in schemas {
            let field_extractions = fields_by_schema.remove(&version).unwrap_or_default();
            let signature_event_types: HashSet<String> = signature_events.into_iter().collect();

            // Detect match mode based on schema version
            let match_mode = if version == "connectivity" {
                "any".to_string()
            } else {
                "all".to_string()
            };

            let special_handling = version == "connectivity";

            let def = SchemaDefinition {
                version: version.clone(),
                description,
                status: status.clone(),
                signature_event_types,
                field_extractions,
                match_mode,
                special_handling,
            };
            defs.push(def);
        }

        // Sort by priority: current > supported > deprecated
        defs.sort_by(|a, b| {
            let priority = |s: &str| match s {
                "current" => 0,
                "supported" => 1,
                "deprecated" => 2,
                _ => 3,
            };
            priority(&a.status).cmp(&priority(&b.status))
        });

        self.schemas = defs.iter().map(|d| (d.version.clone(), d.clone())).collect();
        self.schemas_by_priority = defs;
        self.loaded = true;

        log::info!(
            "SCHEMA_CACHE_LOADED schemas={:?} field_counts={:?}",
            self.schema_versions(),
            self.schemas
                .values()
                .map(|s| (
                    &s.version,
                    s.field_extractions.values().map(|v| v.len()).sum::<usize>()
                ))
                .collect::<Vec<_>>()
        );
    }

    /// Clear the cache.
    pub fn clear(&mut self) {
        self.schemas.clear();
        self.schemas_by_priority.clear();
        self.loaded = false;
    }
}

// Global schema cache with thread-safe access
lazy_static! {
    static ref SCHEMA_CACHE: RwLock<SchemaCache> = RwLock::new(SchemaCache::new());
}

/// Get a read-only reference to the global schema cache.
pub fn get_schema_cache() -> std::sync::RwLockReadGuard<'static, SchemaCache> {
    SCHEMA_CACHE.read().expect("Schema cache lock poisoned")
}

/// Get a mutable reference to the global schema cache.
pub fn get_schema_cache_mut() -> std::sync::RwLockWriteGuard<'static, SchemaCache> {
    SCHEMA_CACHE.write().expect("Schema cache lock poisoned")
}

/// Schema validation result.
#[derive(Debug)]
pub struct SchemaValidationResult {
    pub version: Option<String>,
    pub valid: bool,
    pub reason: Option<String>,
    pub event_types: HashSet<String>,
}

impl SchemaValidationResult {
    pub fn valid(version: &str, event_types: HashSet<String>) -> Self {
        Self {
            version: Some(version.to_string()),
            valid: true,
            reason: None,
            event_types,
        }
    }

    pub fn invalid(reason: &str, event_types: HashSet<String>) -> Self {
        Self {
            version: None,
            valid: false,
            reason: Some(reason.to_string()),
            event_types,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_matches_all() {
        let schema = SchemaDefinition {
            version: "1.9.3".to_string(),
            description: "test".to_string(),
            status: "current".to_string(),
            signature_event_types: HashSet::from([
                "THOUGHT_START".to_string(),
                "DMA_RESULTS".to_string(),
            ]),
            field_extractions: HashMap::new(),
            match_mode: "all".to_string(),
            special_handling: false,
        };

        // Should match when all signature events present
        let events = HashSet::from([
            "THOUGHT_START".to_string(),
            "DMA_RESULTS".to_string(),
            "ACTION_RESULT".to_string(),
        ]);
        assert!(schema.matches(&events));

        // Should not match when missing signature event
        let events = HashSet::from(["THOUGHT_START".to_string()]);
        assert!(!schema.matches(&events));
    }

    #[test]
    fn test_schema_matches_any() {
        let schema = SchemaDefinition {
            version: "connectivity".to_string(),
            description: "test".to_string(),
            status: "current".to_string(),
            signature_event_types: HashSet::from([
                "startup".to_string(),
                "shutdown".to_string(),
            ]),
            field_extractions: HashMap::new(),
            match_mode: "any".to_string(),
            special_handling: true,
        };

        // Should match when any signature event present
        let events = HashSet::from(["startup".to_string()]);
        assert!(schema.matches(&events));

        let events = HashSet::from(["shutdown".to_string()]);
        assert!(schema.matches(&events));

        // Should not match when no signature events
        let events = HashSet::from(["other".to_string()]);
        assert!(!schema.matches(&events));
    }
}
