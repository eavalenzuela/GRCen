"""Tests for TOTP-based MFA: service, login flow, setup UI, recovery codes."""

import uuid

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services import totp_service
from grcen.services.auth import create_user


# ── service layer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_begin_enrollment_creates_pending_row(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, codes = await totp_service.begin_enrollment(pool, u.id)
    assert len(secret) >= 16
    assert len(codes) == 8
    enrolled = await totp_service.get_enrollment(pool, u.id)
    assert enrolled is not None
    assert enrolled["enabled"] is False
    # Stored recovery codes are hashed, not plaintext
    assert codes[0] not in enrolled["recovery_codes"]


@pytest.mark.asyncio
async def test_confirm_wrong_code_fails(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await totp_service.begin_enrollment(pool, u.id)
    ok = await totp_service.confirm_enrollment(pool, u.id, "000000")
    assert ok is False
    assert not await totp_service.is_enabled(pool, u.id)


@pytest.mark.asyncio
async def test_confirm_correct_code_enables(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    code = pyotp.TOTP(secret).now()
    ok = await totp_service.confirm_enrollment(pool, u.id, code)
    assert ok is True
    assert await totp_service.is_enabled(pool, u.id)


@pytest.mark.asyncio
async def test_verify_login_code_accepts_totp(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())
    assert await totp_service.verify_login_code(pool, u.id, pyotp.TOTP(secret).now())


@pytest.mark.asyncio
async def test_verify_login_rejects_wrong_code(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())
    assert not await totp_service.verify_login_code(pool, u.id, "000000")


@pytest.mark.asyncio
async def test_recovery_code_accepted_and_consumed(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, codes = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())

    first = codes[0]
    assert await totp_service.verify_login_code(pool, u.id, first)
    # Second use fails — single-use
    assert not await totp_service.verify_login_code(pool, u.id, first)

    enrolled = await totp_service.get_enrollment(pool, u.id)
    assert len(enrolled["recovery_codes"]) == 7


@pytest.mark.asyncio
async def test_verify_login_requires_enabled(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    # Not confirmed → enabled=false → login verify must fail even with a correct code
    assert not await totp_service.verify_login_code(
        pool, u.id, pyotp.TOTP(secret).now()
    )


@pytest.mark.asyncio
async def test_disable_removes_row(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())
    await totp_service.disable(pool, u.id)
    assert await totp_service.get_enrollment(pool, u.id) is None


# ── login flow ───────────────────────────────────────────────────────────


async def _fresh_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_login_without_mfa_unchanged(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    from tests.conftest import login_with_csrf

    async with await _fresh_client() as c:
        await login_with_csrf(c, u.username, "pw")
        # If MFA weren't required, we should already be authenticated
        home = await c.get("/", follow_redirects=False)
        assert home.status_code in (200, 302)
        # A protected page should work
        me = await c.get("/assets")
        assert me.status_code == 200


@pytest.mark.asyncio
async def test_login_with_mfa_redirects_to_challenge(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())

    from tests.conftest import _extract_csrf_from_session_cookie, get_csrf_token
    async with await _fresh_client() as c:
        csrf = await get_csrf_token(c)
        resp = await c.post(
            "/login",
            data={"username": u.username, "password": "pw", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login/mfa"

        # /assets should still 401/302 — session doesn't hold a session_id yet
        guarded = await c.get("/assets", follow_redirects=False)
        assert guarded.status_code in (302, 401)

        # Submit the TOTP code
        await c.get("/login/mfa")  # refresh csrf
        csrf2 = _extract_csrf_from_session_cookie(c)
        resp2 = await c.post(
            "/login/mfa",
            data={"code": pyotp.TOTP(secret).now(), "csrf_token": csrf2},
            follow_redirects=False,
        )
        assert resp2.status_code == 302
        assert resp2.headers["location"] == "/"
        # Now authenticated
        me = await c.get("/assets")
        assert me.status_code == 200


@pytest.mark.asyncio
async def test_mfa_invalid_code_shows_error(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    secret, _ = await totp_service.begin_enrollment(pool, u.id)
    await totp_service.confirm_enrollment(pool, u.id, pyotp.TOTP(secret).now())

    from tests.conftest import _extract_csrf_from_session_cookie, get_csrf_token
    async with await _fresh_client() as c:
        csrf = await get_csrf_token(c)
        await c.post(
            "/login",
            data={"username": u.username, "password": "pw", "csrf_token": csrf},
            follow_redirects=False,
        )
        await c.get("/login/mfa")
        csrf2 = _extract_csrf_from_session_cookie(c)
        resp = await c.post(
            "/login/mfa",
            data={"code": "000000", "csrf_token": csrf2},
            follow_redirects=False,
        )
        # Re-renders the MFA page with an error message
        assert resp.status_code == 200
        assert "Invalid code" in resp.text


@pytest.mark.asyncio
async def test_mfa_page_without_pending_redirects_to_login(pool):
    async with await _fresh_client() as c:
        resp = await c.get("/login/mfa", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


# ── self-service setup ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_shows_setup_button(auth_client):
    resp = await auth_client.get("/settings")
    assert resp.status_code == 200
    assert "Set up MFA" in resp.text


@pytest.mark.asyncio
async def test_mfa_begin_then_confirm_flow(auth_client, pool):
    from tests.conftest import _extract_csrf_from_session_cookie

    csrf = _extract_csrf_from_session_cookie(auth_client)
    begin = await auth_client.post(
        "/settings/mfa/begin", data={"csrf_token": csrf}, follow_redirects=False,
    )
    assert begin.status_code in (302, 303)

    page = await auth_client.get("/settings")
    assert "Verify &amp; enable" in page.text

    # The secret is in the logged-in user's user_totp row
    from grcen.services import access_log_service  # noqa: F401 — just to ensure module imports
    row = await pool.fetchrow(
        "SELECT ut.secret FROM user_totp ut JOIN users u ON u.id = ut.user_id"
        " ORDER BY ut.created_at DESC LIMIT 1"
    )
    assert row is not None
    code = pyotp.TOTP(row["secret"]).now()
    csrf2 = _extract_csrf_from_session_cookie(auth_client)
    confirm = await auth_client.post(
        "/settings/mfa/confirm",
        data={"code": code, "csrf_token": csrf2},
        follow_redirects=False,
    )
    assert confirm.status_code in (302, 303)
    assert "ok:" in confirm.headers["location"]


@pytest.mark.asyncio
async def test_sso_user_cannot_begin_mfa(pool):
    from tests.conftest import login_with_csrf, _extract_csrf_from_session_cookie

    u = await create_user(
        pool, f"sso_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )
    # Flip the user into "SSO-like" state by stamping oidc_sub directly
    await pool.execute(
        "UPDATE users SET oidc_sub = $1 WHERE id = $2", "sub-abc", u.id
    )
    async with await _fresh_client() as c:
        await login_with_csrf(c, u.username, "pw")
        csrf = _extract_csrf_from_session_cookie(c)
        resp = await c.post(
            "/settings/mfa/begin", data={"csrf_token": csrf}, follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "fail:" in resp.headers["location"]
