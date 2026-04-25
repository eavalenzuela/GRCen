"""IP allowlist enforcement on API tokens."""
import uuid

import pytest

from grcen.permissions import Permission
from grcen.services import token_service


@pytest.mark.asyncio
async def test_token_with_no_allowlist_works_anywhere(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "free", [Permission.VIEW.value]
    )
    resp = await auth_client.get(
        "/api/assets/", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_token_with_allowlist_blocks_other_ip(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    # The httpx ASGI test client reports its IP as 127.0.0.1; pin the allowlist
    # to a different address so the token is rejected.
    _, raw = await token_service.create_token(
        pool, user["id"], "locked", [Permission.VIEW.value],
        allowed_ips=["10.0.0.42"],
    )
    resp = await auth_client.get(
        "/api/assets/", headers={"Authorization": f"Bearer {raw}"}
    )
    # Token validation fails → falls back to session auth, which works for
    # auth_client. We need a fresh client without a session to see the 401.
    from httpx import ASGITransport, AsyncClient
    from grcen.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        bare_resp = await bare.get(
            "/api/assets/", headers={"Authorization": f"Bearer {raw}"}
        )
    assert bare_resp.status_code == 401


@pytest.mark.asyncio
async def test_token_with_matching_ip_allowed(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "matching", [Permission.VIEW.value],
        allowed_ips=["127.0.0.1"],
    )
    from httpx import ASGITransport, AsyncClient
    from grcen.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        resp = await bare.get(
            "/api/assets/", headers={"Authorization": f"Bearer {raw}"}
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_validate_token_returns_none_on_ip_mismatch(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "iponly", [Permission.VIEW.value],
        allowed_ips=["10.0.0.1"],
    )
    blocked = await token_service.validate_token(pool, raw, client_ip="192.168.1.1")
    allowed = await token_service.validate_token(pool, raw, client_ip="10.0.0.1")
    assert blocked is None
    assert allowed is not None


@pytest.mark.asyncio
async def test_cidr_v4_range_allows_in_subnet(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "cidr-v4", [Permission.VIEW.value],
        allowed_ips=["10.0.0.0/24"],
    )
    inside = await token_service.validate_token(pool, raw, client_ip="10.0.0.42")
    outside = await token_service.validate_token(pool, raw, client_ip="10.0.1.1")
    assert inside is not None
    assert outside is None


@pytest.mark.asyncio
async def test_cidr_v6_range_allows_in_subnet(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "cidr-v6", [Permission.VIEW.value],
        allowed_ips=["2001:db8::/32"],
    )
    inside = await token_service.validate_token(pool, raw, client_ip="2001:db8::1")
    outside = await token_service.validate_token(pool, raw, client_ip="2001:dead::1")
    assert inside is not None
    assert outside is None


@pytest.mark.asyncio
async def test_mixed_exact_and_cidr_entries(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "mixed", [Permission.VIEW.value],
        allowed_ips=["192.168.1.5", "10.0.0.0/8"],
    )
    assert (await token_service.validate_token(pool, raw, client_ip="192.168.1.5")) is not None
    assert (await token_service.validate_token(pool, raw, client_ip="10.5.5.5")) is not None
    assert (await token_service.validate_token(pool, raw, client_ip="172.16.0.1")) is None


@pytest.mark.asyncio
async def test_malformed_entry_is_skipped_not_fatal(pool, auth_client):
    """A typo in the allowlist must not lock the entire token out."""
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    _, raw = await token_service.create_token(
        pool, user["id"], "typo", [Permission.VIEW.value],
        allowed_ips=["not-an-ip", "10.0.0.0/24"],
    )
    assert (await token_service.validate_token(pool, raw, client_ip="10.0.0.5")) is not None


@pytest.mark.asyncio
async def test_my_token_allowlist_form_persists(pool, auth_client):
    """The /tokens/{id}/allowed-ips form writes through to the token row."""
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    token, _raw = await token_service.create_token(
        pool, user["id"], "form-test", [Permission.VIEW.value]
    )
    resp = await auth_client.post(
        f"/tokens/{token.id}/allowed-ips",
        data={"allowed_ips": "10.0.0.0/24\n192.168.1.5"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    refreshed = await token_service.get_token_by_id(pool, token.id)
    assert refreshed.allowed_ips == ["10.0.0.0/24", "192.168.1.5"]


@pytest.mark.asyncio
async def test_my_token_allowlist_form_rejects_garbage(pool, auth_client):
    """Bogus entries surface a session error instead of silently saving."""
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    token, _ = await token_service.create_token(
        pool, user["id"], "bad", [Permission.VIEW.value]
    )
    await auth_client.post(
        f"/tokens/{token.id}/allowed-ips",
        data={"allowed_ips": "not-an-ip"},
    )
    refreshed = await token_service.get_token_by_id(pool, token.id)
    # Original empty allowlist preserved.
    assert refreshed.allowed_ips == []


@pytest.mark.asyncio
async def test_my_token_allowlist_form_blocks_other_users_token(pool, auth_client):
    """A user can only edit their own token, even if they know the id."""
    from grcen.services.auth import create_user
    other = await create_user(pool, f"o_{uuid.uuid4().hex[:8]}", "x")
    token, _ = await token_service.create_token(
        pool, other.id, "their-token", [Permission.VIEW.value]
    )
    await auth_client.post(
        f"/tokens/{token.id}/allowed-ips",
        data={"allowed_ips": "1.2.3.4"},
    )
    refreshed = await token_service.get_token_by_id(pool, token.id)
    assert refreshed.allowed_ips == []


@pytest.mark.asyncio
async def test_update_allowed_ips(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    token, raw = await token_service.create_token(
        pool, user["id"], "updatable", [Permission.VIEW.value],
    )
    ok = await token_service.update_allowed_ips(pool, token.id, ["1.2.3.4"])
    assert ok is True
    refreshed = await token_service.validate_token(pool, raw, client_ip="9.9.9.9")
    assert refreshed is None
