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
from typing import Any

logger = logging.getLogger(__name__)

# Lazy load spaCy to avoid startup overhead
_nlp = None
_spacy_available = None


def _get_nlp() -> Any:
    """Lazy load spaCy NLP model."""
    global _nlp, _spacy_available  # noqa: PLW0603

    if _spacy_available is False:
        return None

    if _nlp is None:
        try:
            import spacy  # noqa: PLC0415

            try:
                _nlp = spacy.load("en_core_web_sm")
                _spacy_available = True
                logger.info("Loaded spaCy model en_core_web_sm for PII scrubbing")
            except OSError:
                # Model not installed, try to download
                logger.warning("spaCy model not found, attempting download...")
                from spacy.cli import download  # noqa: PLC0415

                download("en_core_web_sm")
                _nlp = spacy.load("en_core_web_sm")
                _spacy_available = True
                logger.info("Downloaded and loaded spaCy model en_core_web_sm")
        except ImportError:
            logger.warning("spaCy not available - PII scrubbing will use regex fallback")
            _spacy_available = False
            return None

    return _nlp


# NER entity types to redact
REDACT_ENTITY_TYPES = {
    "PERSON",      # People, including fictional
    "ORG",         # Organizations
    "GPE",         # Geopolitical entities (countries, cities, states)
    "FAC",         # Facilities (buildings, airports, highways)
    "LOC",         # Non-GPE locations
    "EMAIL",       # Custom - handled by regex
    "PHONE",       # Custom - handled by regex
    "NORP",        # Nationalities, religious/political groups
}

# Keep these entity types (useful for pattern analysis)
KEEP_ENTITY_TYPES = {
    "DATE",        # Dates
    "TIME",        # Times
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

    nlp = _get_nlp()

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


def scrub_dict_recursive(data: Any, depth: int = 0, max_depth: int = 20) -> Any:
    """
    Recursively scrub PII from a dictionary/list structure.

    Only scrubs string values in fields listed in SCRUB_FIELDS.
    """
    if depth > max_depth:
        return data

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in SCRUB_FIELDS and isinstance(value, str):
                result[key] = scrub_text(value)
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


class PIIScrubber:
    """
    PII Scrubber with cryptographic envelope preservation.

    Maintains provenance chain:
    1. Original signature verified at ingest
    2. Hash of original content stored
    3. PII scrubbed from text fields
    4. Scrubbed version signed by CIRISLens
    """

    def __init__(self, scrub_key_path: str | None = None):
        """
        Initialize scrubber with optional signing key.

        Args:
            scrub_key_path: Path to Ed25519 private key for signing scrubbed data.
                           If not provided, will look for CIRISLENS_SCRUB_KEY_PATH env var
                           or generate a warning.
        """
        self.scrub_key_id = "lens-scrub-v1"
        self._signing_key: bytes | None = None

        key_path_str = scrub_key_path or os.getenv("CIRISLENS_SCRUB_KEY_PATH")

        if key_path_str:
            from pathlib import Path  # noqa: PLC0415

            key_path = Path(key_path_str)
            if key_path.exists():
                try:
                    self._signing_key = key_path.read_bytes()
                    logger.info("Loaded CIRISLens scrub signing key from %s", key_path)
                except Exception as e:
                    logger.error("Failed to load scrub signing key: %s", e)
        else:
            logger.warning(
                "No scrub signing key configured. Set CIRISLENS_SCRUB_KEY_PATH "
                "or pass scrub_key_path. Scrubbed traces will not be signed."
            )

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
