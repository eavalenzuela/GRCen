"""Tests for input validation, session management, and security controls."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_asset_empty_name(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "", "status": "active"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_name_too_long(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "x" * 256, "status": "active"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_description_too_long(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "Test", "description": "x" * 10001},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_too_many_tags(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "Test", "tags": [f"tag{i}" for i in range(51)]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_bad_username_chars(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin<script>", "password": "testpass1"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_short_password_rejected(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "validuser", "password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_invalid_role_rejected(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "validuser", "password": "testpass1", "role": "superadmin"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Account lockout tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_lockout_after_failed_attempts(client, pool):
    from grcen.rate_limit import _reset as _reset_rate_limit
    from grcen.services.auth import create_user

    await create_user(pool, "locktest", "correctpassword")

    # Make 5 failed attempts (reset rate limiter each time — testing lockout, not rate limiting)
    for _ in range(5):
        _reset_rate_limit()
        resp = await client.post(
            "/api/auth/login",
            json={"username": "locktest", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    # 6th attempt should be locked out (429)
    _reset_rate_limit()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "locktest", "password": "wrongpass"},
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_lockout_resets_on_success(client, pool):
    from grcen.rate_limit import _reset as _reset_rate_limit
    from grcen.services.auth import create_user

    await create_user(pool, "resettest", "correctpw")

    # Make 3 failed attempts (below threshold of 5)
    for _ in range(3):
        _reset_rate_limit()
        await client.post(
            "/api/auth/login",
            json={"username": "resettest", "password": "wrongpass"},
        )

    # Successful login resets the counter
    _reset_rate_limit()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "resettest", "password": "correctpw"},
    )
    assert resp.status_code == 200

    # Verify counter was reset
    row = await pool.fetchrow(
        "SELECT failed_login_count FROM users WHERE username = $1", "resettest"
    )
    assert row["failed_login_count"] == 0


# ---------------------------------------------------------------------------
# Session management tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_created_on_login(client, pool):
    from grcen.services.auth import create_user

    user = await create_user(pool, "sesstest", "testpass1")

    resp = await client.post(
        "/api/auth/login",
        json={"username": "sesstest", "password": "testpass1"},
    )
    assert resp.status_code == 200

    # Verify a session row exists in the DB
    count = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", user.id
    )
    assert count == 1


@pytest.mark.asyncio
async def test_session_invalidation_on_logout(client, pool):
    from grcen.services.auth import create_user

    user = await create_user(pool, "logouttest", "testpass1")

    await client.post(
        "/api/auth/login",
        json={"username": "logouttest", "password": "testpass1"},
    )

    # Session exists
    count = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", user.id
    )
    assert count == 1

    # Logout
    await client.post("/api/auth/logout")

    # Session should be deleted
    count = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", user.id
    )
    assert count == 0


@pytest.mark.asyncio
async def test_session_idle_timeout(client, pool):
    from grcen.services.auth import create_user

    user = await create_user(pool, "idletest", "testpass1")

    await client.post(
        "/api/auth/login",
        json={"username": "idletest", "password": "testpass1"},
    )

    # Manually set last_active to 31 minutes ago
    await pool.execute(
        "UPDATE sessions SET last_active = $1 WHERE user_id = $2",
        datetime.now(UTC) - timedelta(minutes=31),
        user.id,
    )

    # Next request should fail — session expired
    resp = await client.get("/api/assets/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_absolute_timeout(client, pool):
    from grcen.services.auth import create_user

    user = await create_user(pool, "abstest", "testpass1")

    await client.post(
        "/api/auth/login",
        json={"username": "abstest", "password": "testpass1"},
    )

    # Manually set created_at to 9 hours ago (beyond 8h absolute timeout)
    await pool.execute(
        "UPDATE sessions SET created_at = $1 WHERE user_id = $2",
        datetime.now(UTC) - timedelta(hours=9),
        user.id,
    )

    # Next request should fail
    resp = await client.get("/api/assets/")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Security headers tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")
    assert "camera=()" in resp.headers.get("Permissions-Policy", "")


# ---------------------------------------------------------------------------
# CSRF tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_rejection_on_form_post_without_token(client, pool):
    from grcen.services.auth import create_user

    await create_user(pool, "csrftest", "testpass1")

    # POST login form without CSRF token should be rejected
    resp = await client.post("/login", data={
        "username": "csrftest",
        "password": "testpass1",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_csrf_accepted_with_valid_token(client, pool):
    from grcen.services.auth import create_user

    await create_user(pool, "csrfok", "testpass1")

    # GET login page to seed CSRF token
    resp = await client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    assert match, "CSRF token hidden input not found in login page"
    csrf_token = match.group(1)

    # POST with CSRF token should succeed (or at least not be 403)
    resp = await client.post("/login", data={
        "username": "csrfok",
        "password": "testpass1",
        "csrf_token": csrf_token,
    })
    # Successful login redirects to /
    assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# Login rate limiting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_rapid_requests(client, pool):
    """Two login attempts in quick succession from the same IP should trigger 429."""
    from grcen.services.auth import create_user

    await create_user(pool, "ratelimit", "testpass1")

    # First attempt — should proceed (may fail auth, but not 429)
    resp = await client.post(
        "/api/auth/login",
        json={"username": "ratelimit", "password": "testpass1"},
    )
    assert resp.status_code != 429

    # Second attempt immediately — should be rate-limited
    resp = await client.post(
        "/api/auth/login",
        json={"username": "ratelimit", "password": "testpass1"},
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_login_rate_limit_allows_after_cooldown(client, pool):
    """After the cooldown period, the next login attempt should be allowed."""
    import time
    from grcen import rate_limit
    from grcen.services.auth import create_user

    await create_user(pool, "ratelimit2", "testpass1")

    # First attempt
    resp = await client.post(
        "/api/auth/login",
        json={"username": "ratelimit2", "password": "testpass1"},
    )
    assert resp.status_code != 429

    # Fake the timestamp to simulate cooldown elapsed
    ip = "127.0.0.1"
    rate_limit._last_attempt[f"login:{ip}"] = time.monotonic() - 3.0

    # Should be allowed now
    resp = await client.post(
        "/api/auth/login",
        json={"username": "ratelimit2", "password": "testpass1"},
    )
    assert resp.status_code != 429


# ---------------------------------------------------------------------------
# Injection prevention tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_search_escapes_html(auth_client, pool):
    """Asset names with HTML/JS should be escaped in owner search results."""
    # Create an asset with XSS payload in name
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "person", "name": "'; alert('xss'); //", "status": "active"},
    )
    assert resp.status_code == 201

    resp = await auth_client.get("/api/owner-search?q=alert")
    assert resp.status_code == 200
    html = resp.text
    # The raw payload must NOT appear unescaped
    assert "alert('xss')" not in html
    # It should be HTML-escaped
    assert "&#x27;" in html or "&apos;" in html or "&#39;" in html


@pytest.mark.asyncio
async def test_attachment_delete_idor_blocked(auth_client, pool):
    """Deleting an attachment via a different asset's URL should return 404."""
    # Create two assets
    resp1 = await auth_client.post(
        "/api/assets/", json={"type": "system", "name": "Asset1", "status": "active"},
    )
    asset1_id = resp1.json()["id"]

    resp2 = await auth_client.post(
        "/api/assets/", json={"type": "system", "name": "Asset2", "status": "active"},
    )
    asset2_id = resp2.json()["id"]

    # Create attachment on asset1
    att_resp = await auth_client.post(
        f"/api/assets/{asset1_id}/attachments/",
        json={"name": "secret.pdf", "kind": "file"},
    )
    assert att_resp.status_code == 201
    att_id = att_resp.json()["id"]

    # Try to delete via asset2's URL — should fail
    resp = await auth_client.delete(f"/api/assets/{asset2_id}/attachments/{att_id}")
    assert resp.status_code == 404

    # Verify it still exists via the correct asset
    resp = await auth_client.get(f"/api/assets/{asset1_id}/attachments/")
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_csp_nonce_in_headers(client):
    """CSP header should contain a nonce, not unsafe-inline for scripts."""
    resp = await client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "'nonce-" in csp


@pytest.mark.asyncio
async def test_csp_nonce_changes_per_request(client):
    """Each request should get a different CSP nonce."""
    resp1 = await client.get("/health")
    resp2 = await client.get("/health")
    csp1 = resp1.headers.get("Content-Security-Policy", "")
    csp2 = resp2.headers.get("Content-Security-Policy", "")
    nonce1 = re.search(r"'nonce-([^']+)'", csp1).group(1)
    nonce2 = re.search(r"'nonce-([^']+)'", csp2).group(1)
    assert nonce1 != nonce2


# ---------------------------------------------------------------------------
# HTTPS redirect & HSTS tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_https_redirect_middleware():
    """HTTPSRedirectMiddleware should redirect HTTP to HTTPS."""
    from fastapi import FastAPI
    from grcen.middleware import HTTPSRedirectMiddleware

    test_app = FastAPI()
    test_app.add_middleware(HTTPSRedirectMiddleware)

    @test_app.get("/test")
    async def _test():
        return {"ok": True}

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
        resp = await c.get("/test")
        assert resp.status_code == 301
        assert resp.headers["location"].startswith("https://")


@pytest.mark.asyncio
async def test_https_redirect_respects_x_forwarded_proto():
    """When X-Forwarded-Proto: https, no redirect should occur and HSTS should be set."""
    from fastapi import FastAPI
    from grcen.middleware import HTTPSRedirectMiddleware

    test_app = FastAPI()
    test_app.add_middleware(HTTPSRedirectMiddleware)

    @test_app.get("/test")
    async def _test():
        return {"ok": True}

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
        resp = await c.get("/test", headers={"X-Forwarded-Proto": "https"})
        assert resp.status_code == 200
        assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")


# ---------------------------------------------------------------------------
# Tenant context on unauthenticated routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_context_middleware_clears_ambient_bypass():
    """A request must never inherit a process-wide RLS bypass.

    The suite sets one at import so fixtures can read raw tables; production
    has none. Without this reset the two environments disagree about whether
    RLS is enforced during request handling, which is precisely how a login
    path that 500s in production passed the entire suite.
    """
    from fastapi import FastAPI

    from grcen import tenancy
    from grcen.middleware import TenantContextMiddleware

    test_app = FastAPI()
    test_app.add_middleware(TenantContextMiddleware)

    @test_app.get("/probe")
    async def _probe():
        return {"bypassing": tenancy.is_bypassing(), "org": tenancy.current_org()}

    assert tenancy.is_bypassing() is True  # ambient suite context

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/probe")

    assert resp.json() == {"bypassing": False, "org": ""}
    assert tenancy.is_bypassing() is True  # ...and restored afterwards


@pytest.mark.asyncio
async def test_html_login_writes_audit_under_enforced_rls(client, pool):
    """Login must establish a tenant context before its RLS-protected writes.

    Regression for the d082f49 RLS rollout: the auth routes have no
    get_current_user dependency, so nothing called set_tenant for them and the
    closing audit write was rejected by the policy — a 500 before the session
    cookie was ever emitted, i.e. nobody could log in at all.
    """
    from grcen.services.auth import create_user
    from tests.conftest import login_with_csrf

    user = await create_user(pool, "rlsloginuser", "testpass123")
    await login_with_csrf(client, "rlsloginuser", "testpass123")

    rows = await pool.fetch(
        "SELECT organization_id FROM audit_log "
        "WHERE user_id = $1 AND action = 'login'",
        user.id,
    )
    assert len(rows) == 1, "login audit row missing — the RLS policy rejected it"
    assert rows[0]["organization_id"] == user.organization_id


@pytest.mark.asyncio
async def test_api_login_writes_audit_under_enforced_rls(client, pool):
    """Same regression on the JSON login route."""
    from grcen.services.auth import create_user

    user = await create_user(pool, "rlsapiuser", "testpass123")
    resp = await client.post(
        "/api/auth/login",
        json={"username": "rlsapiuser", "password": "testpass123"},
    )
    assert resp.status_code == 200

    rows = await pool.fetch(
        "SELECT organization_id FROM audit_log "
        "WHERE user_id = $1 AND action = 'login'",
        user.id,
    )
    assert len(rows) == 1
    assert rows[0]["organization_id"] == user.organization_id


@pytest.mark.asyncio
async def test_prepare_connection_skips_set_role_when_rls_disabled(monkeypatch):
    """The grcen_app role only exists when RLS is on.

    init_schema provisions it under the RLS_ENABLED flag, but the acquire hook
    dropped to it unconditionally — so a fresh install with RLS_ENABLED=false
    failed on every non-bypassed acquire, i.e. every authenticated request.
    """
    from grcen import tenancy
    from grcen.config import settings

    executed: list[str] = []

    class _Conn:
        async def execute(self, sql, *args):
            executed.append(sql)

    monkeypatch.setattr(settings, "RLS_ENABLED", False)
    with tenancy.tenant_context("00000000-0000-0000-0000-000000000001"):
        await tenancy.prepare_connection(_Conn())

    assert not any(f"SET ROLE {tenancy.APP_ROLE}" in s for s in executed), (
        "dropped to a role that does not exist with RLS disabled"
    )
    assert any("RESET ROLE" in s for s in executed)


@pytest.mark.asyncio
async def test_prepare_connection_sets_role_when_rls_enabled(monkeypatch):
    from grcen import tenancy
    from grcen.config import settings

    executed: list[str] = []

    class _Conn:
        async def execute(self, sql, *args):
            executed.append(sql)

    monkeypatch.setattr(settings, "RLS_ENABLED", True)
    with tenancy.tenant_context("00000000-0000-0000-0000-000000000001"):
        await tenancy.prepare_connection(_Conn())

    assert any(f"SET ROLE {tenancy.APP_ROLE}" in s for s in executed)


@pytest.mark.asyncio
async def test_apply_policies_is_atomic(pool):
    """A partial re-apply must never leave a table RLS-enabled with no policy.

    Each statement used to autocommit, so between DROP POLICY and CREATE POLICY
    the table was default-deny. With 2+ workers, one is serving traffic while
    another boots and re-applies, so live reads returned nothing and live writes
    failed. Wrapping the re-apply means an interrupted run rolls back whole.
    """
    from grcen import tenancy

    class _FailAfter:
        """Proxy that fails once a CREATE POLICY has been seen."""

        def __init__(self, conn):
            self._conn = conn
            self._created = 0

        async def execute(self, sql, *args):
            if sql.startswith("CREATE POLICY"):
                self._created += 1
                if self._created > 3:
                    raise RuntimeError("boom, mid-apply")
            return await self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    with tenancy.system_context():
        async with pool.acquire() as conn:
            before = await tenancy.protected_tables(conn)
            assert before, "expected policies to be in place at baseline"

            with pytest.raises(RuntimeError, match="boom"):
                await tenancy.apply_policies(_FailAfter(conn))

            after = await tenancy.protected_tables(conn)

    assert after == before, (
        "a failed re-apply left tables unprotected: "
        f"{sorted(before - after)}"
    )
