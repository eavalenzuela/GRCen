"""Tests for saved-searches CRUD, sharing, and page integration."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services import saved_search_service as ss_svc
from grcen.services.auth import create_user


@pytest.fixture
async def other_user(pool):
    return await create_user(
        pool, f"other_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )


# ── service ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_own_saved_search(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    s = await ss_svc.create_saved_search(
        pool, user_id=u.id, name="High risks", path="/assets", query_string="type=risk"
    )
    assert s.href == "/assets?type=risk"

    listed = await ss_svc.list_visible(pool, u.id)
    assert len(listed) == 1
    assert listed[0].name == "High risks"


@pytest.mark.asyncio
async def test_visibility_private_vs_shared(pool, other_user):
    u1 = await create_user(pool, f"u1_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await ss_svc.create_saved_search(
        pool, user_id=u1.id, name="priv", path="/assets", query_string=""
    )
    await ss_svc.create_saved_search(
        pool, user_id=u1.id, name="pub", path="/assets", query_string="", shared=True
    )

    seen_by_other = await ss_svc.list_visible(pool, other_user.id)
    names = {s.name for s in seen_by_other}
    assert "pub" in names
    assert "priv" not in names

    seen_by_owner = await ss_svc.list_visible(pool, u1.id)
    assert {s.name for s in seen_by_owner} == {"priv", "pub"}


@pytest.mark.asyncio
async def test_list_filter_by_path(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await ss_svc.create_saved_search(
        pool, user_id=u.id, name="a-list", path="/assets", query_string=""
    )
    await ss_svc.create_saved_search(
        pool, user_id=u.id, name="risk-list", path="/risk-management", query_string=""
    )
    for_assets = await ss_svc.list_visible(pool, u.id, path="/assets")
    assert len(for_assets) == 1
    assert for_assets[0].name == "a-list"


@pytest.mark.asyncio
async def test_owner_can_delete(pool):
    u = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    s = await ss_svc.create_saved_search(
        pool, user_id=u.id, name="x", path="/assets", query_string=""
    )
    ok = await ss_svc.delete_saved_search(pool, s.id, u.id)
    assert ok is True
    assert await ss_svc.get_saved_search(pool, s.id) is None


@pytest.mark.asyncio
async def test_non_owner_cannot_delete_private(pool, other_user):
    owner = await create_user(pool, f"owner_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    s = await ss_svc.create_saved_search(
        pool, user_id=owner.id, name="priv", path="/assets", query_string=""
    )
    ok = await ss_svc.delete_saved_search(pool, s.id, other_user.id)
    assert ok is False
    # Still exists
    assert await ss_svc.get_saved_search(pool, s.id) is not None


@pytest.mark.asyncio
async def test_admin_can_delete_anyone(pool):
    owner = await create_user(pool, f"owner_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    admin = await create_user(pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    s = await ss_svc.create_saved_search(
        pool, user_id=owner.id, name="priv", path="/assets", query_string=""
    )
    ok = await ss_svc.delete_saved_search(
        pool, s.id, admin.id, is_admin=True
    )
    assert ok is True


# ── REST API ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_create_and_list(auth_client):
    resp = await auth_client.post(
        "/api/saved-searches/",
        json={"name": "filter-x", "path": "/assets", "query_string": "status=active"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "filter-x"
    assert body["href"] == "/assets?status=active"

    listed = await auth_client.get("/api/saved-searches/")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["href"] == "/assets?status=active"


@pytest.mark.asyncio
async def test_api_requires_name_and_path(auth_client):
    resp = await auth_client.post("/api/saved-searches/", json={"name": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_other_user_cannot_delete_private(pool, auth_client):
    # Create via auth_client (admin), then try deleting as viewer
    create_resp = await auth_client.post(
        "/api/saved-searches/",
        json={"name": "priv", "path": "/assets"},
    )
    sid = create_resp.json()["id"]

    viewer = await create_user(
        pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )
    from tests.conftest import login_with_csrf
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await login_with_csrf(c, viewer.username, "pw")
        # Viewer can't see a private search, so the DELETE returns 404 (not 403 —
        # we don't want to leak existence).
        resp = await c.delete(f"/api/saved-searches/{sid}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_filter_by_path(auth_client):
    await auth_client.post(
        "/api/saved-searches/",
        json={"name": "a", "path": "/assets"},
    )
    await auth_client.post(
        "/api/saved-searches/",
        json={"name": "r", "path": "/risk-management"},
    )
    resp = await auth_client.get("/api/saved-searches/?path=/assets")
    assert resp.status_code == 200
    assert [r["name"] for r in resp.json()] == ["a"]


# ── page integration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assets_page_renders_saved_search_partial(auth_client, pool):
    # Start empty — partial still renders the "Save this search" button
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    assert "Save this search" in resp.text


@pytest.mark.asyncio
async def test_assets_page_shows_existing_saved_searches(auth_client, pool):
    # Create as the logged-in admin so they can see it
    r = await auth_client.post(
        "/api/saved-searches/",
        json={"name": "only-risks", "path": "/assets", "query_string": "type=risk"},
    )
    assert r.status_code == 201
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    assert "only-risks" in resp.text
    assert "/assets?type=risk" in resp.text
