"""
Schema Sync Service for CIRISLens.

This module handles loading, syncing, and caching trace schemas from:
1. Local schema files (schemas/ directory)
2. Remote schema repository (e.g., https://schemas.ciris.ai/traces/)
3. Database (cirislens.trace_schemas table)

The schema cache is used by the trace ingestion pipeline for:
- Schema detection (matching event_types to schema versions)
- Field extraction (JSON path resolution for DB columns)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class FieldExtractionRule:
    """Rule for extracting a field from trace component data."""

    field_name: str
    json_path: str
    data_type: str  # string, float, int, boolean, json, timestamp
    required: bool
    db_column: str
    description: str = ""
    fallback_paths: list[str] = field(default_factory=list)
    transform: str | None = None  # e.g., "equals_tool" for tsaspdma_approved


@dataclass
class SchemaDefinition:
    """Definition of a trace schema version."""

    version: str
    description: str
    status: str  # current, supported, deprecated
    signature_event_types: list[str]
    required_event_types: list[str] | None
    optional_event_types: list[str] | None
    field_extractions: dict[str, dict[str, FieldExtractionRule]]
    special_handling: bool = False
    match_mode: str = "all"  # "all" or "any" for signature matching
    routing: dict[str, Any] | None = None
    source_url: str | None = None


@dataclass
class SyncResult:
    """Result of a schema sync operation."""

    synced_count: int
    updated_count: int
    errors: list[str]
    schemas: list[str]


class SchemaCache:
    """In-memory cache for trace schemas.

    Loaded at startup from database, refreshed on sync.
    Used by ingestion pipeline for schema detection and field extraction.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, SchemaDefinition] = {}
        self._schemas_by_priority: list[SchemaDefinition] = []
        self._fields_by_schema_event: dict[tuple[str, str], list[FieldExtractionRule]] = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        """Check if cache has been loaded."""
        return self._loaded

    def schema_versions(self) -> list[str]:
        """Get list of loaded schema versions."""
        return list(self._schemas.keys())

    def get_schema(self, version: str) -> SchemaDefinition | None:
        """Get schema by version."""
        return self._schemas.get(version)

    def schemas_by_priority(self) -> list[SchemaDefinition]:
        """Get schemas ordered by priority (current > supported > deprecated)."""
        return self._schemas_by_priority

    def get_field_rules(
        self, schema_version: str, event_type: str
    ) -> list[FieldExtractionRule]:
        """Get field extraction rules for a schema/event_type combination."""
        return self._fields_by_schema_event.get((schema_version, event_type), [])

    def detect_schema_version(self, event_types: set[str]) -> SchemaDefinition | None:
        """Detect schema version from event types.

        Matches against signature_event_types in priority order.
        """
        for schema in self._schemas_by_priority:
            signature_events = set(schema.signature_event_types)

            # Check match mode
            if schema.match_mode == "any":
                # Any signature event present = match (for connectivity)
                if event_types & signature_events:
                    logger.info(
                        "SCHEMA_MATCHED version=%s events=%s mode=any",
                        schema.version,
                        event_types,
                    )
                    return schema
            else:
                # All signature events must be present (superset check)
                if event_types >= signature_events:
                    logger.info(
                        "SCHEMA_MATCHED version=%s events=%s signature=%s",
                        schema.version,
                        event_types,
                        signature_events,
                    )
                    return schema

        logger.warning(
            "SCHEMA_UNKNOWN events=%s known_schemas=%s",
            event_types,
            self.schema_versions(),
        )
        return None

    def load_from_definitions(self, schemas: list[SchemaDefinition]) -> None:
        """Load schemas from list of definitions."""
        self._schemas = {s.version: s for s in schemas}

        # Sort by priority: current > supported > deprecated
        priority_order = {"current": 0, "supported": 1, "deprecated": 2}
        self._schemas_by_priority = sorted(
            schemas, key=lambda s: priority_order.get(s.status, 3)
        )

        # Build field lookup
        self._fields_by_schema_event = {}
        for schema in schemas:
            for event_type, fields in schema.field_extractions.items():
                key = (schema.version, event_type)
                self._fields_by_schema_event[key] = list(fields.values())

        self._loaded = True
        logger.info(
            "SCHEMA_CACHE_LOADED schemas=%s fields_count=%d",
            self.schema_versions(),
            sum(len(rules) for rules in self._fields_by_schema_event.values()),
        )


# Global schema cache instance
_schema_cache = SchemaCache()


def get_schema_cache() -> SchemaCache:
    """Get the global schema cache instance."""
    return _schema_cache


def parse_field_extraction(
    field_name: str, rule_data: dict[str, Any]
) -> FieldExtractionRule:
    """Parse a field extraction rule from JSON."""
    return FieldExtractionRule(
        field_name=field_name,
        json_path=rule_data.get("path", ""),
        data_type=rule_data.get("type", "string"),
        required=rule_data.get("required", False),
        db_column=rule_data.get("db_column", field_name),
        description=rule_data.get("description", ""),
        fallback_paths=rule_data.get("fallback_paths", []),
        transform=rule_data.get("transform"),
    )


def parse_schema_definition(data: dict[str, Any], source_url: str | None = None) -> SchemaDefinition:
    """Parse a schema definition from JSON."""
    # Parse field extractions
    field_extractions: dict[str, dict[str, FieldExtractionRule]] = {}
    for event_type, fields in data.get("field_extractions", {}).items():
        field_extractions[event_type] = {
            field_name: parse_field_extraction(field_name, rule_data)
            for field_name, rule_data in fields.items()
        }

    return SchemaDefinition(
        version=data["version"],
        description=data.get("description", ""),
        status=data.get("status", "current"),
        signature_event_types=data.get("signature_event_types", []),
        required_event_types=data.get("required_event_types"),
        optional_event_types=data.get("optional_event_types"),
        field_extractions=field_extractions,
        special_handling=data.get("special_handling", False),
        match_mode=data.get("match_mode", "all"),
        routing=data.get("routing"),
        source_url=source_url,
    )


def load_schemas_from_directory(schema_dir: Path) -> list[SchemaDefinition]:
    """Load schemas from local directory.

    Reads index.json and loads each referenced schema file.
    """
    schemas = []
    index_path = schema_dir / "index.json"

    if not index_path.exists():
        logger.warning("SCHEMA_LOAD_FAILED reason=index_not_found path=%s", index_path)
        return schemas

    try:
        with open(index_path) as f:
            index = json.load(f)

        for schema_meta in index.get("schemas", []):
            schema_url = schema_meta.get("url")
            if not schema_url:
                continue

            schema_path = schema_dir / schema_url
            if not schema_path.exists():
                logger.warning(
                    "SCHEMA_LOAD_SKIPPED reason=file_not_found path=%s", schema_path
                )
                continue

            try:
                with open(schema_path) as f:
                    schema_data = json.load(f)

                # Override status from index if provided
                if "status" in schema_meta:
                    schema_data["status"] = schema_meta["status"]

                schema = parse_schema_definition(schema_data, source_url=str(schema_path))
                schemas.append(schema)
                logger.debug(
                    "SCHEMA_LOADED version=%s status=%s", schema.version, schema.status
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.exception(
                    "SCHEMA_PARSE_FAILED path=%s error=%s", schema_path, e
                )

    except (json.JSONDecodeError, OSError) as e:
        logger.exception("SCHEMA_INDEX_FAILED path=%s error=%s", index_path, e)

    logger.info(
        "SCHEMA_LOAD_COMPLETE source=directory count=%d path=%s",
        len(schemas),
        schema_dir,
    )
    return schemas


async def fetch_schemas_from_remote(remote_url: str) -> list[SchemaDefinition]:
    """Fetch schemas from remote repository.

    Fetches index.json and loads each referenced schema.
    """
    schemas = []

    async with aiohttp.ClientSession() as session:
        # Fetch index
        index_url = f"{remote_url.rstrip('/')}/index.json"
        try:
            async with session.get(index_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(
                        "SCHEMA_FETCH_FAILED reason=index_http_error url=%s status=%d",
                        index_url,
                        resp.status,
                    )
                    return schemas
                index = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.exception("SCHEMA_FETCH_FAILED reason=network url=%s error=%s", index_url, e)
            return schemas

        # Fetch each schema
        for schema_meta in index.get("schemas", []):
            schema_url = schema_meta.get("url")
            if not schema_url:
                continue

            full_url = f"{remote_url.rstrip('/')}/{schema_url}"
            try:
                async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "SCHEMA_FETCH_SKIPPED reason=http_error url=%s status=%d",
                            full_url,
                            resp.status,
                        )
                        continue
                    schema_data = await resp.json()

                # Override status from index
                if "status" in schema_meta:
                    schema_data["status"] = schema_meta["status"]

                schema = parse_schema_definition(schema_data, source_url=full_url)
                schemas.append(schema)
                logger.debug(
                    "SCHEMA_FETCHED version=%s status=%s url=%s",
                    schema.version,
                    schema.status,
                    full_url,
                )
            except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as e:
                logger.exception("SCHEMA_FETCH_SKIPPED reason=error url=%s error=%s", full_url, e)

    logger.info(
        "SCHEMA_FETCH_COMPLETE source=remote count=%d url=%s",
        len(schemas),
        remote_url,
    )
    return schemas


async def sync_schemas_to_database(
    schemas: list[SchemaDefinition],
    conn: Any,  # asyncpg.Connection
) -> SyncResult:
    """Sync schemas to database.

    Upserts schemas and field extraction rules.
    """
    errors: list[str] = []
    synced = 0
    updated = 0
    synced_versions: list[str] = []

    for schema in schemas:
        try:
            # Upsert schema
            result = await conn.execute(
                """
                INSERT INTO cirislens.trace_schemas
                    (version, description, status, definition, signature_event_types,
                     required_event_types, optional_event_types, source_url, synced_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (version) DO UPDATE SET
                    description = EXCLUDED.description,
                    status = EXCLUDED.status,
                    definition = EXCLUDED.definition,
                    signature_event_types = EXCLUDED.signature_event_types,
                    required_event_types = EXCLUDED.required_event_types,
                    optional_event_types = EXCLUDED.optional_event_types,
                    source_url = EXCLUDED.source_url,
                    synced_at = NOW(),
                    updated_at = NOW()
                """,
                schema.version,
                schema.description,
                schema.status,
                json.dumps({
                    "version": schema.version,
                    "description": schema.description,
                    "special_handling": schema.special_handling,
                    "match_mode": schema.match_mode,
                    "routing": schema.routing,
                }),
                schema.signature_event_types,
                schema.required_event_types,
                schema.optional_event_types,
                schema.source_url,
            )

            if "INSERT" in result:
                synced += 1
            else:
                updated += 1

            # Upsert field extraction rules
            for event_type, fields in schema.field_extractions.items():
                for field_name, rule in fields.items():
                    await conn.execute(
                        """
                        INSERT INTO cirislens.trace_schema_fields
                            (schema_version, event_type, field_name, json_path,
                             data_type, required, db_column, description)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (schema_version, event_type, field_name) DO UPDATE SET
                            json_path = EXCLUDED.json_path,
                            data_type = EXCLUDED.data_type,
                            required = EXCLUDED.required,
                            db_column = EXCLUDED.db_column,
                            description = EXCLUDED.description
                        """,
                        schema.version,
                        event_type,
                        field_name,
                        rule.json_path,
                        rule.data_type,
                        rule.required,
                        rule.db_column,
                        rule.description,
                    )

            synced_versions.append(schema.version)
            logger.debug("SCHEMA_SYNCED version=%s status=%s", schema.version, schema.status)

        except Exception as e:
            error_msg = f"Failed to sync schema {schema.version}: {e}"
            errors.append(error_msg)
            logger.exception("SCHEMA_SYNC_FAILED version=%s error=%s", schema.version, e)

    logger.info(
        "SCHEMA_SYNC_COMPLETE synced=%d updated=%d errors=%d",
        synced,
        updated,
        len(errors),
    )
    return SyncResult(
        synced_count=synced,
        updated_count=updated,
        errors=errors,
        schemas=synced_versions,
    )


async def load_schemas_from_database(conn: Any) -> list[SchemaDefinition]:
    """Load schemas from database.

    Used to populate cache at startup.
    """
    schemas = []

    # Load schemas
    rows = await conn.fetch(
        """
        SELECT version, description, status, definition,
               signature_event_types, required_event_types, optional_event_types, source_url
        FROM cirislens.trace_schemas
        WHERE status != 'deprecated' OR status IS NULL
        ORDER BY CASE status
            WHEN 'current' THEN 0
            WHEN 'supported' THEN 1
            ELSE 2
        END
        """
    )

    # Load all field rules
    field_rows = await conn.fetch(
        """
        SELECT schema_version, event_type, field_name, json_path,
               data_type, required, db_column, description
        FROM cirislens.trace_schema_fields
        """
    )

    # Group field rules by (schema_version, event_type)
    fields_by_schema_event: dict[tuple[str, str], dict[str, FieldExtractionRule]] = {}
    for row in field_rows:
        key = (row["schema_version"], row["event_type"])
        if key not in fields_by_schema_event:
            fields_by_schema_event[key] = {}
        fields_by_schema_event[key][row["field_name"]] = FieldExtractionRule(
            field_name=row["field_name"],
            json_path=row["json_path"],
            data_type=row["data_type"],
            required=row["required"],
            db_column=row["db_column"],
            description=row["description"] or "",
        )

    # Build schema definitions
    for row in rows:
        definition = row["definition"] or {}
        field_extractions: dict[str, dict[str, FieldExtractionRule]] = {}

        # Get field rules for this schema
        for (schema_ver, event_type), fields in fields_by_schema_event.items():
            if schema_ver == row["version"]:
                field_extractions[event_type] = fields

        schema = SchemaDefinition(
            version=row["version"],
            description=row["description"] or "",
            status=row["status"] or "current",
            signature_event_types=row["signature_event_types"] or [],
            required_event_types=row["required_event_types"],
            optional_event_types=row["optional_event_types"],
            field_extractions=field_extractions,
            special_handling=definition.get("special_handling", False),
            match_mode=definition.get("match_mode", "all"),
            routing=definition.get("routing"),
            source_url=row["source_url"],
        )
        schemas.append(schema)

    logger.info("SCHEMA_DB_LOAD_COMPLETE count=%d", len(schemas))
    return schemas


async def initialize_schema_cache(conn: Any, schema_dir: Path | None = None) -> None:
    """Initialize the schema cache.

    1. Try to load from database
    2. If empty, load from local directory and sync to database
    3. Populate the global cache
    """
    cache = get_schema_cache()

    # Try database first
    schemas = await load_schemas_from_database(conn)

    if not schemas and schema_dir:
        # Database empty, seed from local files
        logger.info("SCHEMA_CACHE_SEEDING source=local_directory path=%s", schema_dir)
        schemas = load_schemas_from_directory(schema_dir)
        if schemas:
            await sync_schemas_to_database(schemas, conn)

    if schemas:
        cache.load_from_definitions(schemas)
    else:
        logger.warning("SCHEMA_CACHE_EMPTY no schemas loaded")


async def refresh_schema_cache(conn: Any) -> None:
    """Refresh the schema cache from database."""
    cache = get_schema_cache()
    schemas = await load_schemas_from_database(conn)
    cache.load_from_definitions(schemas)
    logger.info("SCHEMA_CACHE_REFRESHED count=%d", len(schemas))
