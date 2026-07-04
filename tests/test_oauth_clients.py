"""Tests for the OAuth 2.0 client-credentials flow."""

import uuid

import pytest

from grcen.permissions import UserRole
from grcen.services import oauth_service
from grcen.services.auth import create_user


@pytest.fixture
async def admin(pool):
    return await create_user(
        pool, f"oadmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _client(pool, admin, permissions=None, ttl=3600):
    return await oauth_service.create_client(
        pool,
        organization_id=admin.organization_id,
        name="ci-pipeline",
        permissions=permissions or ["view"],
        token_ttl_seconds=ttl,
        created_by=admin.id,
    )


# ── service layer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_token_with_valid_credentials(pool, admin):
    _, client_id, secret = await _client(pool, admin)
    issued = await oauth_service.issue_token(pool, client_id, secret)
    assert issued is not None
    raw, expires_in = issued
    assert raw.startswith("grcen_")
    assert expires_in == 3600


@pytest.mark.asyncio
async def test_wrong_secret_rejected(pool, admin):
    _, client_id, _ = await _client(pool, admin)
    assert await oauth_service.issue_token(pool, client_id, "wrong") is None
    assert await oauth_service.issue_token(pool, "nonexistent", "x") is None


@pytest.mark.asyncio
async def test_disabled_client_rejected(pool, admin):
    client, client_id, secret = await _client(pool, admin)
    await oauth_service.set_enabled(
        pool, client.id, False, organization_id=admin.organization_id
    )
    assert await oauth_service.issue_token(pool, client_id, secret) is None


@pytest.mark.asyncio
async def test_ttl_is_clamped(pool, admin):
    _, client_id, secret = await _client(pool, admin, ttl=999999)
    _, expires_in = await oauth_service.issue_token(pool, client_id, secret)
    assert expires_in == 24 * 3600


# ── the token endpoint + using the issued token ──────────────────────────


@pytest.mark.asyncio
async def test_token_endpoint_full_flow(client, pool, admin):
    _, client_id, secret = await _client(pool, admin, permissions=["view"])
    resp = await client.post(
        "/api/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert resp.headers["cache-control"] == "no-store"

    # The issued token works as a normal bearer token, scoped to 'view'.
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    listing = await client.get("/api/assets/", headers=headers)
    assert listing.status_code == 200
    denied = await client.post(
        "/api/assets/", json={"type": "system", "name": "X"}, headers=headers
    )
    assert denied.status_code == 403  # token lacks 'create'


@pytest.mark.asyncio
async def test_token_endpoint_error_shapes(client, pool, admin):
    _, client_id, secret = await _client(pool, admin)
    resp = await client.post(
        "/api/oauth/token",
        data={"grant_type": "password", "client_id": client_id, "client_secret": secret},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"

    resp = await client.post(
        "/api/oauth/token", data={"grant_type": "client_credentials"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"

    resp = await client.post(
        "/api/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": "bad",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


# ── admin page ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_page_create_and_list(auth_client):
    resp = await auth_client.post(
        "/admin/oauth-clients",
        data={"name": "scanner", "permissions": "view", "token_ttl_seconds": "600"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    page = await auth_client.get("/admin/oauth-clients")
    assert "scanner" in page.text
    assert "client_secret" in page.text  # shown-once block


@pytest.mark.asyncio
async def test_admin_page_requires_admin(viewer_client):
    resp = await viewer_client.get("/admin/oauth-clients")
    assert resp.status_code == 403
