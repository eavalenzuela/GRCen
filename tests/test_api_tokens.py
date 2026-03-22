import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services.auth import create_user
from grcen.services import token_service


@pytest_asyncio.fixture
async def admin_user(pool):
    return await create_user(pool, f"admin_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN)


@pytest_asyncio.fixture
async def editor_user(pool):
    return await create_user(pool, f"editor_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.EDITOR)


@pytest_asyncio.fixture
async def viewer_user(pool):
    return await create_user(pool, f"viewer_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.VIEWER)


@pytest_asyncio.fixture
async def admin_session(pool, admin_user):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": admin_user.username, "password": "testpass"})
        yield c


@pytest_asyncio.fixture
async def editor_session(pool, editor_user):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": editor_user.username, "password": "testpass"})
        yield c


@pytest_asyncio.fixture
async def viewer_session(pool, viewer_user):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": viewer_user.username, "password": "testpass"})
        yield c


# --- Token CRUD ---


@pytest.mark.asyncio
async def test_create_and_list_token(admin_session, pool):
    resp = await admin_session.post(
        "/api/tokens/",
        json={"name": "test-token", "permissions": ["view", "create"]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-token"
    assert "token" in data
    assert data["token"].startswith("grcen_")
    assert set(data["permissions"]) == {"view", "create"}
    assert data["is_service_account"] is False

    # List
    resp = await admin_session.get("/api/tokens/")
    assert resp.status_code == 200
    tokens = resp.json()
    assert len(tokens) == 1
    assert tokens[0]["name"] == "test-token"
    # Raw token should NOT be in list response
    assert "token" not in tokens[0] or tokens[0].get("token") is None


@pytest.mark.asyncio
async def test_revoke_token(admin_session, pool):
    resp = await admin_session.post(
        "/api/tokens/", json={"name": "to-revoke", "permissions": ["view"]},
    )
    token_id = resp.json()["id"]

    resp = await admin_session.delete(f"/api/tokens/{token_id}")
    assert resp.status_code == 204

    # List should show revoked
    resp = await admin_session.get("/api/tokens/")
    assert resp.json()[0]["revoked"] is True


@pytest.mark.asyncio
async def test_cannot_create_token_with_excess_permissions(editor_session, pool):
    resp = await editor_session.post(
        "/api/tokens/",
        json={"name": "bad", "permissions": ["manage_users"]},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_empty_permissions_rejected(admin_session, pool):
    resp = await admin_session.post(
        "/api/tokens/", json={"name": "empty", "permissions": []},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_permission_rejected(admin_session, pool):
    resp = await admin_session.post(
        "/api/tokens/", json={"name": "bad", "permissions": ["nonexistent"]},
    )
    assert resp.status_code == 400


# --- Bearer token authentication ---


@pytest.mark.asyncio
async def test_bearer_auth_grants_access(pool, admin_user):
    token, raw = await token_service.create_token(
        pool, admin_user.id, "bearer-test", ["view"],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/assets/", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_revoked_token_returns_401(pool, admin_user):
    token, raw = await token_service.create_token(
        pool, admin_user.id, "revoke-test", ["view"],
    )
    await token_service.revoke_token(pool, token.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/assets/", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_returns_401(pool, admin_user):
    token, raw = await token_service.create_token(
        pool, admin_user.id, "expired-test", ["view"],
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/assets/", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/assets/", headers={"Authorization": "Bearer grcen_bogus123"},
        )
        assert resp.status_code == 401


# --- Permission scoping ---


@pytest.mark.asyncio
async def test_token_permission_scoping(pool, admin_user):
    """A token with only VIEW cannot CREATE, even if the user's role allows it."""
    token, raw = await token_service.create_token(
        pool, admin_user.id, "view-only", ["view"],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # View should work
        resp = await c.get("/api/assets/", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200

        # Create should be forbidden (token lacks 'create' permission)
        resp = await c.post(
            "/api/assets/",
            json={"type": "system", "name": "test"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403


# --- Service account tokens ---


@pytest.mark.asyncio
async def test_service_account_creation_admin_only(editor_session, pool):
    resp = await editor_session.post(
        "/api/tokens/",
        json={"name": "svc", "permissions": ["view"], "is_service_account": True},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_service_account_exempt_from_max_expiry(pool, admin_user):
    await token_service.set_max_expiry_days(pool, 7)

    # Service account can have no expiry
    token, raw = await token_service.create_token(
        pool, admin_user.id, "svc", ["view"],
        is_service_account=True,
    )
    assert token.expires_at is None

    # Regular token gets capped
    token2, _ = await token_service.create_token(
        pool, admin_user.id, "regular", ["view"],
    )
    assert token2.expires_at is not None
    max_allowed = datetime.now(UTC) + timedelta(days=8)
    assert token2.expires_at < max_allowed


# --- Admin max expiry config ---


@pytest.mark.asyncio
async def test_admin_max_expiry_config(admin_session, pool):
    # Set max expiry
    resp = await admin_session.put(
        "/api/tokens/config", json={"max_expiry_days": 30},
    )
    assert resp.status_code == 200
    assert resp.json()["max_expiry_days"] == 30

    # Read it back
    resp = await admin_session.get("/api/tokens/config")
    assert resp.json()["max_expiry_days"] == 30

    # Clear it
    resp = await admin_session.put(
        "/api/tokens/config", json={"max_expiry_days": None},
    )
    assert resp.json()["max_expiry_days"] is None


@pytest.mark.asyncio
async def test_non_admin_cannot_access_config(editor_session, pool):
    resp = await editor_session.get("/api/tokens/config")
    assert resp.status_code == 403


# --- Admin can manage all tokens (including other admins') ---


@pytest.mark.asyncio
async def test_admin_can_list_all_tokens(pool, admin_user, editor_user):
    await token_service.create_token(pool, admin_user.id, "admin-t", ["view"])
    await token_service.create_token(pool, editor_user.id, "editor-t", ["view"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": admin_user.username, "password": "testpass"})
        resp = await c.get("/api/tokens/all")
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert "admin-t" in names
        assert "editor-t" in names


@pytest.mark.asyncio
async def test_admin_can_revoke_other_admins_token(pool):
    admin1 = await create_user(pool, f"a1_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN)
    admin2 = await create_user(pool, f"a2_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN)

    token, _ = await token_service.create_token(pool, admin2.id, "a2-token", ["view"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": admin1.username, "password": "testpass"})
        resp = await c.delete(f"/api/tokens/all/{token.id}")
        assert resp.status_code == 204


# --- OpenAPI docs require auth ---


@pytest.mark.asyncio
async def test_docs_unauthenticated_returns_401(pool):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/docs")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_docs_authenticated_returns_200(admin_session, pool):
    resp = await admin_session.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_redoc_authenticated_returns_200(admin_session, pool):
    resp = await admin_session.get("/redoc")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openapi_json_accessible(admin_session, pool):
    resp = await admin_session.get("/api/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "BearerAuth" in schema.get("components", {}).get("securitySchemes", {})
