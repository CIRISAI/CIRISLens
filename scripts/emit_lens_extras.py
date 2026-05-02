#!/usr/bin/env python3
"""
Emit `LensExtras` JSON for the lens build manifest.

Invoked from CI before `ciris-build-sign` to produce the primitive-
specific extras blob the BuildManifest references. Reads the source
tree directly (api/main.py for routes, sql/ for migrations,
api/requirements.txt for the persist pin, the model bundle for its
hash) so the extras are deterministic per checkout.

Usage:

    python3 scripts/emit_lens_extras.py > lens-extras.json

Output is compact JSON on stdout. Non-zero exit = something broke;
the JSON in stdout is incomplete and MUST NOT be signed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "api"
SQL_DIR = REPO_ROOT / "sql"
MODEL_DIR = REPO_ROOT / "scripts"  # placeholder; CI may set CIRIS_LENS_MODEL_DIR
DEFAULT_MODEL_DIR = "/build/models/distilbert_int8"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fastapi_routes() -> list[dict[str, object]]:
    """Import api.main and walk app.routes to capture the public surface.

    LensExtras carries the canonical route list so federation peers
    can verify "this is the lens with these endpoints" without
    cracking the image. The shape is `[{path, methods, endpoint}]`
    sorted deterministically by (path, methods).
    """
    sys.path.insert(0, str(API_DIR))
    try:
        # Importing main.py at extras-emission time runs its module-
        # level startup gates (AV-6 production OAuth check, etc).
        # For build-time emission we set ENV=build to skip the
        # IS_PRODUCTION-gated SystemExit; the canonical routes are a
        # static property of the source, not a runtime concern.
        os.environ.setdefault("ENV", "build")
        # OAUTH_CLIENT_ID stays default ("mock-client-id"); IS_PRODUCTION
        # is False because ENV != "production"; AV-6 gate doesn't fire.

        import main  # noqa: PLC0415
    except Exception as e:  # pragma: no cover — CI must show why
        print(f"emit_lens_extras: failed to import api.main: {e}", file=sys.stderr)
        raise

    rows: list[dict[str, object]] = []
    for route in main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not path or not methods or not endpoint:
            continue  # mounts / WebSocket / etc.
        endpoint_qualname = f"{endpoint.__module__}.{endpoint.__qualname__}"
        rows.append({
            "path": path,
            "methods": sorted(methods),
            "endpoint": endpoint_qualname,
        })
    rows.sort(key=lambda r: (r["path"], ",".join(r["methods"])))  # type: ignore[index]
    return rows


def _sql_migrations() -> list[dict[str, str]]:
    """Numbered SQL migrations under sql/, hashed for tamper detection.

    The lens has accumulated 27+ numbered migrations over time. Pinning
    each file's sha256 lets a verifier confirm the lens deploy applied
    the same DDL the build manifest claims, byte-for-byte.
    """
    rows: list[dict[str, str]] = []
    for f in sorted(SQL_DIR.glob("*.sql")):
        rows.append({
            "filename": f.name,
            "sha256": _sha256_file(f),
        })
    return rows


def _persist_ref() -> str:
    """Pinned ciris-persist version from api/requirements.txt.

    Tells federation peers "this lens links against persist X.Y.Z"
    without cracking the image. Cross-references the persist build
    manifest for the same version — composes cleanly with persist's
    own provenance chain.
    """
    req = (API_DIR / "requirements.txt").read_text()
    for raw in req.splitlines():
        line = raw.strip()
        if line.startswith("ciris-persist=="):
            return line.split("==", 1)[1].strip()
    return "<unpinned>"


def _scrubber_model_sha256() -> dict[str, str]:
    """Hash of the bundled NER model files.

    Pinning the hashes of the DistilBERT INT8 ONNX model (and tokenizer
    + config) lets a verifier probe whether a deployment is on the
    canonical model bundle vs a substitute. ~130 MB total; the hashes
    are cheap to compute once at build time.

    CI sets CIRIS_LENS_MODEL_DIR to /build/models/distilbert_int8
    where the model_builder Dockerfile stage emits it. Dev runs
    without the model bundle return an empty dict — the manifest
    still signs cleanly, just without the pin.
    """
    model_dir = Path(os.environ.get("CIRIS_LENS_MODEL_DIR", DEFAULT_MODEL_DIR))
    if not model_dir.is_dir():
        return {}
    hashes: dict[str, str] = {}
    for f in sorted(model_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(model_dir).as_posix()
            hashes[rel] = _sha256_file(f)
    return hashes


def main() -> int:
    extras = {
        "primitive": "lens",
        "fastapi_routes": _fastapi_routes(),
        "sql_migrations": _sql_migrations(),
        "persist_ref": _persist_ref(),
        "scrubber_model_sha256": _scrubber_model_sha256(),
    }
    json.dump(extras, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
