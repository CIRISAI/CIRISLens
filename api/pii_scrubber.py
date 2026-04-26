"""
PII Scrubber for CIRIS Covenant Traces

Scrubs personally identifiable information from full_traces level data
while preserving the cryptographic envelope for provenance.

Flow:
1. Verify original agent signature
2. Hash original message content
3. Scrub PII using NER
4. Re-sign scrubbed version with CIRISLens key
5. Original PII is never persisted

Reference: https://ciris.ai/privacy
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy load spaCy to avoid startup overhead
_nlp = None             # English NER (en_core_web_sm)
_nlp_xx = None          # Multilingual NER (xx_ent_wiki_sm) — fallback for non-Latin text
_spacy_available = None


def _has_non_latin(text: str) -> bool:
    """Detect substantial non-Latin content. The English NER model only
    recognizes entities in Latin-script text; CJK/Arabic/Cyrillic content
    must be processed by the multilingual model to get any entity coverage."""
    if not text:
        return False
    non_latin = sum(1 for c in text if ord(c) > 0x024F)  # past Latin Extended-B
    return non_latin >= max(3, len(text) // 20)  # >5% non-Latin or >=3 chars


def _get_nlp(text: str | None = None) -> Any:
    """Lazy load spaCy NLP model. Returns the multilingual model when the
    text contains substantial non-Latin script; otherwise the English model."""
    global _nlp, _nlp_xx, _spacy_available  # noqa: PLW0603

    if _spacy_available is False:
        return None

    if _nlp is None:
        try:
            import spacy  # noqa: PLC0415
            try:
                _nlp = spacy.load("en_core_web_sm")
                _spacy_available = True
                logger.info("Loaded spaCy en_core_web_sm")
            except OSError:
                from spacy.cli import download  # noqa: PLC0415
                download("en_core_web_sm")
                _nlp = spacy.load("en_core_web_sm")
                _spacy_available = True
                logger.info("Downloaded en_core_web_sm")
            # Try multilingual model as a secondary; non-fatal if missing
            try:
                _nlp_xx = spacy.load("xx_ent_wiki_sm")
                logger.info("Loaded spaCy xx_ent_wiki_sm (multilingual)")
            except OSError:
                try:
                    from spacy.cli import download  # noqa: PLC0415
                    download("xx_ent_wiki_sm")
                    _nlp_xx = spacy.load("xx_ent_wiki_sm")
                    logger.info("Downloaded xx_ent_wiki_sm (multilingual)")
                except Exception as e:
                    logger.warning(
                        "Multilingual NER unavailable (xx_ent_wiki_sm): %s; "
                        "non-Latin text will fall back to English NER + regex.", e
                    )
                    _nlp_xx = None
        except ImportError:
            logger.warning("spaCy not available - PII scrubbing will use regex fallback")
            _spacy_available = False
            return None

    # Choose model based on text content
    if text is not None and _nlp_xx is not None and _has_non_latin(text):
        return _nlp_xx
    return _nlp


# NER entity types to redact
# Rationale for DATE/TIME: a historical year combined with a redacted entity
# can still uniquely identify the event. Year and time references are now
# primary scrubbing categories.
# Rationale for MISC: multilingual NER models (xx_ent_wiki_sm) tag entities
# they can't classify finely as MISC; treat as redactable to avoid leaking
# named entities in non-Latin scripts.
REDACT_ENTITY_TYPES = {
    "PERSON",      # People, including fictional
    "ORG",         # Organizations
    "GPE",         # Geopolitical entities (countries, cities, states)
    "FAC",         # Facilities (buildings, airports, highways)
    "LOC",         # Non-GPE locations
    "EMAIL",       # Custom - handled by regex
    "PHONE",       # Custom - handled by regex
    "NORP",        # Nationalities, religious/political groups
    "DATE",        # Dates and historical years (was KEEP — promoted to REDACT)
    "TIME",        # Times
    "EVENT",       # Named events (battles, wars, agreements)
    "MISC",        # Multilingual NER catch-all for unclassified named entities
    "WORK_OF_ART", # Titles of works
    "LAW",         # Named legal documents
}

# Keep these entity types — purely numeric, low re-identification risk
KEEP_ENTITY_TYPES = {
    "MONEY",       # Monetary values
    "PERCENT",     # Percentages
    "QUANTITY",    # Quantities
    "ORDINAL",     # Ordinal numbers
    "CARDINAL",    # Cardinal numbers
}

# Text fields that need PII scrubbing in full_traces
SCRUB_FIELDS = [
    # THOUGHT_START
    "task_description",
    "initial_context",
    "thought_content",         # was missing — top leak source in pre-fix corpus
    # SNAPSHOT_AND_CONTEXT
    "system_snapshot",
    "gathered_context",
    "relevant_memories",
    "conversation_history",
    # DMA_RESULTS
    "reasoning",
    "prompt_used",
    "combined_analysis",
    # ASPDMA_RESULT
    "action_rationale",
    "reasoning_summary",
    "action_parameters",
    "aspdma_prompt",
    # CONSCIENCE_RESULT
    "conscience_override_reason",
    "epistemic_data",
    "updated_status_content",
    "entropy_reason",
    "coherence_reason",
    "optimization_veto_justification",
    "epistemic_humility_justification",
    # ACTION_RESULT
    "execution_error",
    # IDMA_RESULT — these fields can contain topic-revealing source identifiers
    # and free-text reasoning that the original scrubber missed.
    "intervention_recommendation",
    "next_best_recovery_step",
    "correlation_factors",
    "top_correlation_factors",
    "common_cause_flags",
    "sources_identified",
    "source_ids",
    "source_clusters",
    "source_types",
    "source_type_counts",
    "pairwise_correlation_summary",
    "reasoning_state",
    # DMA structured-output fields that carry topic-revealing flag/conflict text
    "flags",                   # dma_results.{csdma,dsdma,pdma}.flags
    "alignment_check",         # dma_results.pdma.alignment_check
    "conflicts",               # dma_results.pdma.conflicts (also pdma_conflicts top-level)
    "stakeholders",            # dma_results.pdma.stakeholders
    "questions",               # action_result.action_parameters.questions
    "completion_reason",       # action_result.action_parameters.completion_reason
    "current_thought_summary", # snapshot_and_context.system_snapshot.current_thought_summary
    "epistemic_humility_uncertainties",
]

# Regex patterns for entities spaCy might miss
REGEX_PATTERNS = [
    # Email addresses
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),
    # Phone numbers (various formats)
    (r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE]'),
    # IP addresses
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP_ADDRESS]'),
    # URLs with potential PII in path
    (r'https?://[^\s]+', '[URL]'),
    # Social security numbers (US format)
    (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]'),
    # Credit card numbers (basic pattern)
    (r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CREDIT_CARD]'),
    # Historical years (1700-2023). Excludes 2024-2026 to preserve current
    # timestamps in conversation. Catches bare years that NER's DATE entity
    # may miss (e.g., a bare four-digit year without surrounding context).
    (r'\b(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])\b', '[YEAR]'),
    # Decade references in any language transliteration ("1980s", "the 80s",
    # "1980年代"). Same year-range constraint.
    (r'\b(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])s?\b年?代?', '[YEAR]'),
    # Programmatic identifiers that embed a historical year as a token
    # component (e.g., agent-constructed source IDs). Catches the whole
    # compound identifier rather than just the year, since the surrounding
    # tokens encode the topic. Conservative bound: identifier is at most
    # 80 chars to avoid over-eager matches across sentence boundaries.
    (r'\b[\w\-]{0,40}(?:1[7-9]\d{2}|20[0-1]\d|202[0-3])[\w\-]{0,40}\b', '[IDENTIFIER]'),
]


def scrub_text_regex_only(text: str) -> str:
    """Fallback regex-only scrubbing when spaCy unavailable."""
    if not text or not isinstance(text, str):
        return text

    result = text

    # Apply regex patterns
    for pattern, replacement in REGEX_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def scrub_text(text: str) -> str:
    """
    Scrub PII from text using NER and regex patterns.

    Replaces entities with typed placeholders:
    - [PERSON_1], [PERSON_2], etc.
    - [ORG_1], [ORG_2], etc.
    - [EMAIL], [PHONE], etc.

    Returns the scrubbed text.
    """
    if not text or not isinstance(text, str):
        return text

    # Pick English vs multilingual NER based on text content
    nlp = _get_nlp(text)

    if nlp is None:
        # Fallback to regex only
        return scrub_text_regex_only(text)

    # Process with spaCy
    doc = nlp(text)

    # Track entity counts for unique placeholders
    entity_counts: dict[str, int] = {}

    # Collect replacements (reverse order to preserve positions)
    replacements: list[tuple[int, int, str]] = []

    for ent in doc.ents:
        if ent.label_ in REDACT_ENTITY_TYPES:
            # Generate placeholder
            count = entity_counts.get(ent.label_, 0) + 1
            entity_counts[ent.label_] = count
            placeholder = f"[{ent.label_}_{count}]"
            replacements.append((ent.start_char, ent.end_char, placeholder))

    # Apply replacements in reverse order
    result = text
    for start, end, placeholder in sorted(replacements, reverse=True):
        result = result[:start] + placeholder + result[end:]

    # Apply regex patterns for things spaCy might miss
    for pattern, replacement in REGEX_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def _scrub_value(value: Any) -> Any:
    """Apply scrub_text to a value, recursively for lists/dicts of strings.

    Used when the parent key matched SCRUB_FIELDS — at that point every
    string in the subtree is in scope for scrubbing, regardless of nested
    structure (lists of strings, dicts mapping to strings, mixed)."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


def scrub_dict_recursive(data: Any, depth: int = 0, max_depth: int = 20) -> Any:
    """
    Recursively scrub PII from a dictionary/list structure.

    When a key in SCRUB_FIELDS is encountered, EVERY string in that subtree
    is scrubbed — including elements of lists-of-strings (e.g., a programmatic
    source identifier in a list of strings) which the previous version
    missed because list elements have no key to match on.
    """
    if depth > max_depth:
        return data

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in SCRUB_FIELDS:
                # Match — scrub the whole subtree
                result[key] = _scrub_value(value)
            elif isinstance(value, (dict, list)):
                result[key] = scrub_dict_recursive(value, depth + 1, max_depth)
            else:
                result[key] = value
        return result

    elif isinstance(data, list):
        return [scrub_dict_recursive(item, depth + 1, max_depth) for item in data]

    else:
        return data


def hash_content(content: str | bytes) -> str:
    """Generate SHA-256 hash of content."""
    if isinstance(content, str):
        content = content.encode('utf-8')
    return hashlib.sha256(content).hexdigest()


def sign_content(content: str | bytes, signing_key_bytes: bytes) -> str:
    """Sign content with Ed25519 key, return base64 signature."""
    try:
        from nacl.signing import SigningKey  # noqa: PLC0415

        if isinstance(content, str):
            content = content.encode('utf-8')

        signing_key = SigningKey(signing_key_bytes)
        signed = signing_key.sign(content)
        return base64.urlsafe_b64encode(signed.signature).decode('ascii')
    except Exception as e:
        logger.error("Failed to sign content: %s", e)
        return ""


def _generate_scrub_key() -> bytes:
    """Generate a new Ed25519 signing key (32 bytes seed)."""
    try:
        from nacl.signing import SigningKey  # noqa: PLC0415

        key = SigningKey.generate()
        return bytes(key)
    except ImportError:
        # Fallback to os.urandom if nacl not available
        return os.urandom(32)


class PIIScrubber:
    """
    PII Scrubber with cryptographic envelope preservation.

    Maintains provenance chain:
    1. Original signature verified at ingest
    2. Hash of original content stored
    3. PII scrubbed from text fields
    4. Scrubbed version signed by CIRISLens
    """

    DEFAULT_KEY_PATH = "/data/keys/scrub_signing.key"

    def __init__(self, scrub_key_path: str | None = None):
        """
        Initialize scrubber with signing key (auto-generates if missing).

        Args:
            scrub_key_path: Path to Ed25519 private key for signing scrubbed data.
                           If not provided, will look for CIRISLENS_SCRUB_KEY_PATH env var
                           or use default path. Key is auto-generated if missing.
        """
        self.scrub_key_id = "lens-scrub-v1"
        self._signing_key: bytes | None = None

        key_path_str = (
            scrub_key_path
            or os.getenv("CIRISLENS_SCRUB_KEY_PATH")
            or self.DEFAULT_KEY_PATH
        )
        key_path = Path(key_path_str)

        # Try to load existing key
        if key_path.exists():
            try:
                key_data = key_path.read_bytes()
                self._signing_key = self._parse_key_data(key_data, key_path)
                if self._signing_key:
                    logger.info("Loaded CIRISLens scrub signing key from %s", key_path)
                else:
                    self._signing_key = self._create_and_save_key(key_path)
            except Exception as e:
                logger.error("Failed to load scrub signing key: %s", e)
                self._signing_key = self._create_and_save_key(key_path)
        else:
            # Key doesn't exist - create it
            logger.info("No scrub signing key found at %s. Generating new key.", key_path)
            self._signing_key = self._create_and_save_key(key_path)

    def _parse_key_data(self, key_data: bytes, key_path: Path) -> bytes | None:
        """Parse key data, accepting both raw 32-byte keys and base64-encoded keys."""
        # Strip whitespace/newlines
        key_data = key_data.strip()

        # If exactly 32 bytes, it's raw
        if len(key_data) == 32:
            return key_data

        # Try base64 decode
        try:
            # Try standard base64
            decoded = base64.b64decode(key_data)
            if len(decoded) == 32:
                logger.info("Decoded base64 scrub key from %s", key_path)
                return decoded
        except Exception:  # noqa: S110
            pass  # Expected for non-base64 keys

        try:
            # Try URL-safe base64
            decoded = base64.urlsafe_b64decode(key_data)
            if len(decoded) == 32:
                logger.info("Decoded URL-safe base64 scrub key from %s", key_path)
                return decoded
        except Exception:  # noqa: S110
            pass  # Expected for non-base64 keys

        # Invalid key
        logger.warning(
            "Scrub key at %s has invalid format (size=%d bytes after strip). "
            "Expected 32 raw bytes or 44-char base64. Regenerating.",
            key_path,
            len(key_data),
        )
        return None

    def _create_and_save_key(self, key_path: Path) -> bytes | None:
        """Generate a new signing key and save it to disk."""
        try:
            # Ensure parent directory exists
            key_path.parent.mkdir(parents=True, exist_ok=True)

            # Generate new key
            new_key = _generate_scrub_key()

            # Save with restricted permissions
            key_path.write_bytes(new_key)
            key_path.chmod(0o600)

            logger.info("Generated and saved new scrub signing key to %s", key_path)

            # Log the public key for registration
            self._log_public_key(new_key)

            return new_key
        except Exception as e:
            logger.error("Failed to create scrub signing key at %s: %s", key_path, e)
            return None

    def _log_public_key(self, private_key: bytes) -> None:
        """Log the public key for database registration."""
        try:
            from nacl.signing import SigningKey  # noqa: PLC0415

            signing_key = SigningKey(private_key)
            public_key = signing_key.verify_key
            public_key_b64 = base64.urlsafe_b64encode(bytes(public_key)).decode('ascii')

            logger.info(
                "SCRUB_KEY_PUBLIC: key_id=%s public_key=%s",
                self.scrub_key_id,
                public_key_b64,
            )
            logger.info(
                "Register this key in the database with:\n"
                "INSERT INTO cirislens.lens_signing_keys (key_id, public_key, created_at) "
                "VALUES ('%s', '%s', NOW()) ON CONFLICT (key_id) DO UPDATE SET public_key = EXCLUDED.public_key;",
                self.scrub_key_id,
                public_key_b64,
            )
        except ImportError:
            logger.warning("nacl not available - cannot log public key")
        except Exception as e:
            logger.error("Failed to log public key: %s", e)

    def scrub_trace(
        self,
        trace_data: dict[str, Any],
        original_signature_verified: bool,
        original_message: str | bytes,
    ) -> dict[str, Any]:
        """
        Scrub PII from a full_traces level trace.

        Args:
            trace_data: The trace data dict (will be modified in place)
            original_signature_verified: Whether the original signature was valid
            original_message: The original message that was signed (for hashing)

        Returns:
            Dict with scrubbed data and cryptographic envelope fields:
            - original_content_hash: SHA-256 of original message
            - scrub_timestamp: When scrubbing occurred
            - scrub_signature: CIRISLens signature of scrubbed content
            - scrub_key_id: ID of the signing key used
        """
        # Hash the original content before any modification
        original_hash = hash_content(original_message)

        # Scrub PII from components
        if "components" in trace_data:
            trace_data["components"] = [
                self._scrub_component(comp) for comp in trace_data["components"]
            ]

        # Add cryptographic envelope
        scrub_timestamp = datetime.now(UTC).isoformat()

        envelope = {
            "original_content_hash": original_hash,
            "original_signature_verified": original_signature_verified,
            "scrub_timestamp": scrub_timestamp,
            "scrub_key_id": self.scrub_key_id,
            "scrub_signature": None,
        }

        # Sign the scrubbed content if we have a key
        if self._signing_key:
            # Create canonical message from scrubbed components
            scrubbed_message = json.dumps(
                trace_data.get("components", []),
                sort_keys=True
            ).encode('utf-8')
            envelope["scrub_signature"] = sign_content(
                scrubbed_message, self._signing_key
            )

        trace_data.update(envelope)
        return trace_data

    def _scrub_component(self, component: dict[str, Any]) -> dict[str, Any]:
        """Scrub PII from a single trace component."""
        if not isinstance(component, dict):
            return component

        result = component.copy()

        # Scrub the data field recursively
        if "data" in result:
            result["data"] = scrub_dict_recursive(result["data"])

        return result

    def should_scrub(self, trace_level: str) -> bool:
        """Check if trace level requires PII scrubbing."""
        return trace_level == "full_traces"


# Global scrubber instance (lazy initialized)
_scrubber: PIIScrubber | None = None


def get_scrubber() -> PIIScrubber:
    """Get or create the global PII scrubber instance."""
    global _scrubber  # noqa: PLW0603
    if _scrubber is None:
        _scrubber = PIIScrubber()
    return _scrubber


def scrub_full_trace(
    trace_data: dict[str, Any],
    original_signature_verified: bool,
    original_message: str | bytes,
) -> dict[str, Any]:
    """
    Convenience function to scrub a full_traces level trace.

    Args:
        trace_data: The trace data dict
        original_signature_verified: Whether original signature was valid
        original_message: Original signed message (for hashing)

    Returns:
        Scrubbed trace data with cryptographic envelope
    """
    return get_scrubber().scrub_trace(
        trace_data,
        original_signature_verified,
        original_message,
    )
