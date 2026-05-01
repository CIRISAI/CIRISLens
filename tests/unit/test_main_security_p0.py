"""
Tests for the P0 security hot-fix bundle (THREAT_MODEL.md AV-6/7/13).

- AV-6: production startup gate against the mock-OAuth bypass
- AV-7: OAuth state CSRF binding on login -> callback round-trip
- AV-13: 8 MiB body cap on /api/v1/accord/events ingest path
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Add api to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))


@pytest.fixture
def client() -> TestClient:
    """TestClient bound to the in-process app (mock-OAuth dev mode)."""
    from main import app

    return TestClient(app)


# ─── AV-6: production startup gate ────────────────────────────────────


class TestAV6ProductionStartupGate:
    """The mock-OAuth branch must be unreachable in production deployments."""

    def test_module_import_raises_in_production_with_default_oauth_client_id(self):
        """
        Importing main.py with ENV=production AND OAUTH_CLIENT_ID unset
        must raise SystemExit. Subprocess-isolated because the import
        gate is module-level — re-importing in this process would not
        re-fire it.
        """
        repo_root = Path(__file__).parent.parent.parent
        # S603/S607: sys.executable + literal list, no shell, no untrusted input.
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'api'); import main",
            ],
            cwd=str(repo_root),
            env={
                "PATH": "/usr/bin:/bin",
                "ENV": "production",
                # Deliberately do NOT set OAUTH_CLIENT_ID — exercises the gate
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "OAUTH_CLIENT_ID" in result.stderr
        assert "mock-client-id" in result.stderr
        assert "anonymous admin" in result.stderr.lower()

    def test_module_import_succeeds_in_production_with_real_oauth_client_id(self):
        """Production with a real OAUTH_CLIENT_ID must import cleanly."""
        repo_root = Path(__file__).parent.parent.parent
        # S603/S607: sys.executable + literal list, no shell, no untrusted input.
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'api'); import main; print('ok')",
            ],
            cwd=str(repo_root),
            env={
                "PATH": "/usr/bin:/bin",
                "ENV": "production",
                "OAUTH_CLIENT_ID": "1234567890.apps.googleusercontent.com",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "ok" in result.stdout

    def test_dev_mode_unaffected(self):
        """ENV unset → dev → mock-OAuth path remains operational."""
        # If we got this far, we already imported main.py without ENV=production
        from main import IS_PRODUCTION, OAUTH_CLIENT_ID

        assert IS_PRODUCTION is False
        # Dev default; the mock branch is reachable here, which is correct
        assert OAUTH_CLIENT_ID == "mock-client-id"


# ─── AV-7: OAuth state CSRF binding ──────────────────────────────────


class TestAV7OAuthStateCSRF:
    """The login → callback flow must round-trip a one-time random state."""

    def test_callback_rejects_missing_cookie(self, client: TestClient):
        """No oauth_state cookie at callback → 400 OAuth state mismatch."""
        # Ensure no oauth_state cookie is set
        client.cookies.clear()
        response = client.get(
            "/api/admin/auth/callback?code=any&state=any",
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "OAuth state mismatch" in response.text

    def test_callback_rejects_missing_query_param(self, client: TestClient):
        """oauth_state cookie present but no state query param → 400."""
        client.cookies.set("oauth_state", "valid-state-token")
        response = client.get(
            "/api/admin/auth/callback?code=any",
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_callback_rejects_mismatch(self, client: TestClient):
        """cookie != query param → 400."""
        client.cookies.set("oauth_state", "real-state-from-login")
        response = client.get(
            "/api/admin/auth/callback?code=any&state=tampered",
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "OAuth state mismatch" in response.text

    def test_login_sets_oauth_state_cookie_in_production_branch(
        self, client: TestClient, monkeypatch
    ):
        """Production OAuth login must set oauth_state cookie + include
        state in the redirect URL."""
        import main

        # Force the production-OAuth branch via patching the module-level
        # constant (the startup gate runs at import time so we can't
        # re-import; patching after import is the right shape for unit
        # coverage).
        monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "real-prod-client.apps.googleusercontent.com")

        response = client.get("/api/admin/auth/login", follow_redirects=False)
        assert response.status_code in (302, 307)

        # State cookie set
        set_cookie = response.headers.get("set-cookie", "")
        assert "oauth_state=" in set_cookie
        assert "HttpOnly" in set_cookie

        # state query param present in Google redirect URL
        location = response.headers.get("location", "")
        assert "accounts.google.com" in location
        assert "state=" in location

    def test_login_in_mock_mode_does_not_set_state_cookie(self, client: TestClient):
        """Dev/mock branch creates a session directly; no state cookie needed."""
        client.cookies.clear()
        response = client.get("/api/admin/auth/login", follow_redirects=False)
        assert response.status_code == 302  # mock branch redirects to admin
        set_cookie = response.headers.get("set-cookie", "")
        assert "oauth_state=" not in set_cookie
        # Session cookie SHOULD be set in mock mode
        assert "session_id=" in set_cookie

    def test_state_token_is_random_per_call(self, client: TestClient, monkeypatch):
        """Two consecutive logins must produce different state tokens."""
        import main

        monkeypatch.setattr(main, "OAUTH_CLIENT_ID", "real-prod-client.apps.googleusercontent.com")

        r1 = client.get("/api/admin/auth/login", follow_redirects=False)
        r2 = client.get("/api/admin/auth/login", follow_redirects=False)
        # Pull state out of the location URL
        from urllib.parse import parse_qs, urlparse

        s1 = parse_qs(urlparse(r1.headers["location"]).query)["state"][0]
        s2 = parse_qs(urlparse(r2.headers["location"]).query)["state"][0]
        assert s1 != s2
        assert len(s1) >= 32  # secrets.token_urlsafe(32) yields ≥43 chars


# ─── AV-13: body-size cap on ingest path ─────────────────────────────


class TestAV13BodySizeCap:
    """The /accord/events POST middleware must reject oversized bodies
    before reading them into memory."""

    def test_oversized_body_rejected_with_413(self, client: TestClient):
        """Body exceeding MAX_INGEST_BODY_BYTES → 413."""
        from main import MAX_INGEST_BODY_BYTES

        oversized = b"{}" + b" " * (MAX_INGEST_BODY_BYTES + 1)
        response = client.post(
            "/api/v1/accord/events",
            content=oversized,
            headers={
                "Content-Length": str(len(oversized)),
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 413
        body = response.json()
        assert "exceeds" in body["detail"].lower()
        assert body["max_bytes"] == MAX_INGEST_BODY_BYTES

    def test_malformed_content_length_rejected_with_400(self, client: TestClient):
        """Non-integer Content-Length → 400."""
        response = client.post(
            "/api/v1/accord/events",
            content=b"{}",
            headers={
                "Content-Length": "not-an-integer",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 400
        assert "Content-Length" in response.json()["detail"]

    def test_negative_content_length_rejected_with_413(self, client: TestClient):
        """Negative Content-Length → 413 (treated as out-of-bound)."""
        response = client.post(
            "/api/v1/accord/events",
            content=b"{}",
            headers={
                "Content-Length": "-1",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 413

    def test_at_limit_body_passes_size_gate(self, client: TestClient):
        """Body exactly at MAX_INGEST_BODY_BYTES passes the size gate
        (downstream may still 422; this test asserts the gate alone)."""
        from main import MAX_INGEST_BODY_BYTES

        # Pad a minimally-valid JSON to exactly the limit. The handler
        # will reject downstream (validation/db unavailable) but NOT 413.
        padding_size = MAX_INGEST_BODY_BYTES - len(b'{"events":[]}')
        body = b'{"events":[]' + b" " * padding_size + b"}"
        # Adjust to land precisely on the limit
        body = body[:MAX_INGEST_BODY_BYTES]
        response = client.post(
            "/api/v1/accord/events",
            content=body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "application/json",
            },
        )
        # The middleware lets it through; the route handler / pydantic
        # may 422 on schema. The point: NOT 413.
        assert response.status_code != 413

    def test_non_events_routes_unaffected(self, client: TestClient):
        """Body cap is scoped to /accord/events POST; other routes pass."""
        # /health is GET, and /v1/status/history is GET. Just confirm
        # the middleware doesn't interfere with unrelated GETs.
        response = client.get("/health")
        assert response.status_code == 200
