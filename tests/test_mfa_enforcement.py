"""Per-role MFA enforcement at login."""
import uuid

import pyotp
import pytest

from grcen.config import settings
from grcen.permissions import UserRole
from grcen.services import totp_service
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest.mark.asyncio
async def test_login_without_mfa_blocked_when_role_required(pool, client):
    """A user in a required-MFA role can't log in until they enroll."""
    settings.MFA_REQUIRED_FOR_ROLES = "admin"
    try:
        await create_user(pool, "needs_mfa", "pw", role=UserRole.ADMIN)
        # Simulate the login form submission directly so we can see the
        # rejection text.
        from grcen.rate_limit import _reset
        _reset()
        csrf = await _get_csrf(client)
        resp = await client.post(
            "/login",
            data={"username": "needs_mfa", "password": "pw", "csrf_token": csrf},
            follow_redirects=False,
        )
        # Login form re-rendered with the enforcement error.
        assert resp.status_code == 200
        assert "Two-factor authentication is required" in resp.text
        # No session cookie issued — verify by hitting an authenticated route.
        guarded = await client.get("/", follow_redirects=False)
        assert guarded.status_code in (302, 401)
    finally:
        settings.MFA_REQUIRED_FOR_ROLES = ""


@pytest.mark.asyncio
async def test_login_passes_when_role_has_mfa(pool, client):
    """Same role, but MFA enrolled → login proceeds to /login/mfa."""
    settings.MFA_REQUIRED_FOR_ROLES = "admin"
    try:
        user = await create_user(pool, "has_mfa", "pw", role=UserRole.ADMIN)
        secret, _ = await totp_service.begin_enrollment(pool, user.id)
        await totp_service.confirm_enrollment(pool, user.id, pyotp.TOTP(secret).now())
        from grcen.rate_limit import _reset
        _reset()
        csrf = await _get_csrf(client)
        resp = await client.post(
            "/login",
            data={"username": "has_mfa", "password": "pw", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login/mfa"
    finally:
        settings.MFA_REQUIRED_FOR_ROLES = ""


@pytest.mark.asyncio
async def test_unenforced_role_still_signs_in_without_mfa(pool, client):
    """Roles not listed in MFA_REQUIRED_FOR_ROLES skip the gate."""
    settings.MFA_REQUIRED_FOR_ROLES = "admin"
    try:
        await create_user(pool, "viewer_no_mfa", "pw", role=UserRole.VIEWER)
        from grcen.rate_limit import _reset
        _reset()
        csrf = await _get_csrf(client)
        resp = await client.post(
            "/login",
            data={"username": "viewer_no_mfa", "password": "pw", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
    finally:
        settings.MFA_REQUIRED_FOR_ROLES = ""


async def _get_csrf(client):
    """Tiny duplicate of conftest helper (we need it without the full login)."""
    from tests.conftest import _extract_csrf_from_html, _extract_csrf_from_session_cookie
    resp = await client.get("/login")
    token = _extract_csrf_from_html(resp.text)
    if not token:
        token = _extract_csrf_from_session_cookie(client)
    return token
