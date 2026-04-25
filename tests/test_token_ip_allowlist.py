"""IP allowlist enforcement on API tokens."""
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
async def test_update_allowed_ips(pool, auth_client):
    user = await pool.fetchrow("SELECT id FROM users LIMIT 1")
    token, raw = await token_service.create_token(
        pool, user["id"], "updatable", [Permission.VIEW.value],
    )
    ok = await token_service.update_allowed_ips(pool, token.id, ["1.2.3.4"])
    assert ok is True
    refreshed = await token_service.validate_token(pool, raw, client_ip="9.9.9.9")
    assert refreshed is None
