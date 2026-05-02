"""
Federation directory mirror for agent public-key registrations.

When the lens registers an agent's Ed25519 trace-signing key (POST
/api/v1/accord/public-keys), this module mirrors the registration
into `cirislens.federation_keys` alongside the existing
`cirislens.accord_public_keys` INSERT — so the federation directory
that consumers read from (CIRISRegistry, peer lenses, agents querying
peer keys) is populated as agents come online, without a separate
backfill step per registration.

## Why mirror instead of swap

Persist v0.2.1+'s Backend trait dual-reads federation_keys first then
falls back to accord_public_keys; the trace-verify hot path picks up
either one transparently. So the mirror is *additive* — accord_public_keys
remains load-bearing during the v0.2.x/v0.3.x migration window, the
federation directory comes online incrementally, and the legacy
write drops at v0.4.0 when persist retires the dual-read fallback.

## Trust chain

Every federation_keys row carries its own scrub envelope. Bootstrap
rows (the lens-steward row itself) are self-signed; all others chain
to a steward key. For agent rows the lens publishes:

- `scrub_key_id = "lens-steward"` — the lens vouches for this
  registration (it's "a key the lens accepted", not "a key with its
  own steward chain"; the latter belongs to the agent's deployment
  story).
- `scrub_signature_classical` — Ed25519 signature over the canonical
  registration envelope, signed with the lens-steward Ed25519 key
  via `engine.steward_sign()`. Same FFI-boundary discipline as
  `engine.sign()`: the lens process never touches the seed.
- `scrub_signature_pqc = None` initially. Cold path picks it up via
  `attach_key_pqc_signature` once the ML-DSA-65 sign completes —
  schema permits the pending state explicitly per FEDERATION_DIRECTORY.md
  §"Trust contract".

## Best-effort, not load-bearing

The mirror is wrapped in a try/except — if `engine.put_public_key`
fails (e.g. lens-steward bootstrap row not yet present in
federation_keys, transient backend hiccup), the registration still
succeeds via the accord_public_keys path. The error is logged at
WARNING so operators see it, but the agent's registration POST
returns 200 either way. Rationale: the legacy table is what verify
reads in the failure mode; the directory is convenience, not
correctness.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

import persist_engine

logger = logging.getLogger(__name__)

# v0.2.2 federation directory only allows algorithm="hybrid". Schema
# enforces it via CHECK constraint; persist's KeyRecord serde rejects
# anything else. Hybrid means "Ed25519 component + ML-DSA-65 component";
# rows can ship Ed25519-only initially with PQC filled in cold-path.
_ALGORITHM_HYBRID = "hybrid"

# Identity classification for agent registrations. Persist supports
# four values: agent | primitive | steward | partner. Agent is what
# the lens's POST /accord/public-keys handler is for.
_IDENTITY_TYPE_AGENT = "agent"


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with `+00:00` offset.

    Persist's serde DateTime<Utc> deserializer accepts either `Z` or
    `+00:00`; we use the explicit offset because it round-trips through
    Python `datetime.fromisoformat` cleanly without the 3.10 vs 3.11
    'Z' parsing quirks.
    """
    return datetime.now(UTC).isoformat()


def _build_registration_envelope(
    *,
    key_id: str,
    public_key_base64: str,
    description: str | None,
) -> dict[str, object]:
    """The free-form JSON object the lens canonicalizes + signs.

    Persist treats `registration_envelope` as opaque storage — it's
    canonicalized + hashed for the integrity bytes (`original_content_hash`,
    `scrub_signature_classical`) but not parsed semantically. We pick a
    minimal shape that names:

    - the role (`registrar`: "lens") — so consumers walking the chain
      know who admitted this key,
    - what was registered (key_id + the pubkey itself),
    - when (`registered_at`),
    - the operator's free-form `description` if any.

    Field order doesn't matter — persist's PythonJsonDumpsCanonicalizer
    sorts keys before serialization, so any order produces the same
    canonical bytes.
    """
    envelope: dict[str, object] = {
        "registrar": "lens",
        "role": "agent_trace_signing",
        "key_id": key_id,
        "pubkey_ed25519_base64": public_key_base64,
        "registered_at": _utc_now_iso(),
    }
    if description:
        envelope["description"] = description
    return envelope


def mirror_agent_registration(
    *,
    key_id: str,
    public_key_base64: str,
    description: str | None,
) -> bool:
    """Mirror an agent registration into `cirislens.federation_keys`.

    Returns True if the mirror succeeded, False if it was skipped or
    failed. Never raises — callers wrap their own legacy-path INSERT
    around this; the mirror is best-effort, not load-bearing.

    Parameters mirror the existing `register_public_key` handler shape:
    `key_id` becomes `federation_keys.key_id`, `public_key_base64`
    becomes `federation_keys.pubkey_ed25519_base64`, `description` (if
    present) is included verbatim in the canonical envelope.
    """
    if not persist_engine.steward_ready():
        # Steward not configured — quiet skip. Logged once at engine
        # init; per-call logging here would be noise.
        return False

    engine = persist_engine.get_engine()
    if engine is None:
        # Engine init failed earlier — already logged.
        return False

    try:
        envelope = _build_registration_envelope(
            key_id=key_id,
            public_key_base64=public_key_base64,
            description=description,
        )
        envelope_json = json.dumps(envelope)

        # Canonicalize via persist's own canonicalizer — never re-implement
        # the rules client-side (CIRISPersist#7 drift trap).
        canonical = engine.canonicalize_envelope(envelope_json)

        original_content_hash = hashlib.sha256(canonical).hexdigest()

        # Hot-path Ed25519 sign with the lens-steward identity. Returns
        # 64 raw bytes; persist expects standard base64 (88 chars).
        import base64  # noqa: PLC0415  — lazy; only on the mirror path
        sig_raw = engine.steward_sign(canonical)
        sig_b64 = base64.b64encode(sig_raw).decode("ascii")

        scrub_key_id = engine.steward_key_id()  # "lens-steward" by default

        now_iso = _utc_now_iso()
        record = {
            "key_id": key_id,
            "pubkey_ed25519_base64": public_key_base64,
            # PQC fields ship NULL; cold path fills via attach_*.
            # `pubkey_ml_dsa_65_base64` and `scrub_signature_pqc` are
            # both `Option<String>` with `skip_serializing_if`, so
            # omitting them produces the same canonical bytes as
            # writing `null`.
            "algorithm": _ALGORITHM_HYBRID,
            "identity_type": _IDENTITY_TYPE_AGENT,
            "identity_ref": key_id,
            "valid_from": now_iso,
            # `valid_until` omitted = no expiry.
            "registration_envelope": envelope,
            "original_content_hash": original_content_hash,
            "scrub_signature_classical": sig_b64,
            "scrub_key_id": scrub_key_id,
            "scrub_timestamp": now_iso,
            # `pqc_completed_at` omitted while hybrid-pending.
            # `persist_row_hash` ignored on write (server-computed).
            "persist_row_hash": "",
        }
        engine.put_public_key(json.dumps({"record": record}))
    except Exception as e:
        # Best-effort. The most likely failure modes are:
        # - lens-steward bootstrap row not yet present in
        #   federation_keys (FK violation). Bridge ships the bootstrap
        #   script separately; until run, mirror writes will fail for
        #   every agent. The legacy accord_public_keys path still
        #   succeeds — verify still works via persist's dual-read.
        # - transient backend hiccup (connection blip, serialization
        #   conflict on concurrent put_public_key for the same key).
        # In every case, accord_public_keys is the load-bearing write;
        # this mirror is convenience for the federation directory.
        logger.warning(
            "federation_mirror: failed to mirror %s into federation_keys: %s",
            key_id,
            e,
        )
        return False
    else:
        logger.info(
            "federation_mirror: registered %s in federation_keys "
            "(scrub_key_id=%s, hybrid-pending PQC)",
            key_id,
            scrub_key_id,
        )
        return True
