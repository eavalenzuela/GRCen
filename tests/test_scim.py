"""Tests for SCIM 2.0 user provisioning (roadmap I1).

SCIM endpoints authenticate with a GRCen API token scoped to the ``sync``
permission; the token's org scopes provisioning.
"""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services.auth import create_user, get_user_by_id
from grcen.services.token_service import create_token


async def _sync_token(pool) -> str:
    admin = await create_user(
        pool, f"scimadmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    _, raw = await create_token(pool, admin.id, "scim", ["sync"], is_service_account=True)
    return raw


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _payload(username: str, **over) -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": username,
        "displayName": over.get("display_name", "Ada Lovelace"),
        "externalId": over.get("external_id", f"ext-{username}"),
        "active": over.get("active", True),
        "emails": [{"value": over.get("email", f"{username}@example.com"), "primary": True}],
        "title": "Engineer",
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
            "employeeNumber": over.get("employee_id", "E123"),
            "department": "Engineering",
        },
    }


@pytest.mark.asyncio
async def test_requires_sync_permission(pool):
    async with _client() as c:
        # unauthenticated → 401
        r = await c.post("/scim/v2/Users", json=_payload("nobody"))
        assert r.status_code == 401
        # token lacking the sync scope → 403
        admin = await create_user(pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
        _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)
        r = await c.post(
            "/scim/v2/Users", json=_payload("nobody2"),
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_provisions_user_and_person(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        r = await c.post("/scim/v2/Users", json=_payload("ada"), headers=h)
        assert r.status_code == 201, r.text
        assert r.headers["content-type"].startswith("application/scim+json")
        body = r.json()
        assert body["userName"] == "ada"
        assert body["active"] is True
        assert body["externalId"] == "ext-ada"

        user = await get_user_by_id(pool, uuid.UUID(body["id"]))
        assert user is not None
        assert user.person_asset_id is not None
        prow = await pool.fetchrow(
            "SELECT type, source, source_ref, metadata, status FROM assets WHERE id = $1",
            user.person_asset_id,
        )
        assert prow["type"] == "person"
        assert prow["source"] == "scim"
        assert prow["source_ref"] == "ext-ada"
        assert prow["status"] == "active"
        meta = prow["metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else meta
        assert meta.get("employee_id") == "E123"
        assert meta.get("department") == "Engineering"


@pytest.mark.asyncio
async def test_create_duplicate_conflicts(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        assert (await c.post("/scim/v2/Users", json=_payload("dup"), headers=h)).status_code == 201
        r2 = await c.post("/scim/v2/Users", json=_payload("dup"), headers=h)
        assert r2.status_code == 409
        assert r2.json()["scimType"] == "uniqueness"


@pytest.mark.asyncio
async def test_missing_username_is_400(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        r = await c.post("/scim/v2/Users", json={"displayName": "No Name"}, headers=h)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_and_list_filter(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        created = (await c.post("/scim/v2/Users", json=_payload("bob"), headers=h)).json()
        uid = created["id"]

        g = await c.get(f"/scim/v2/Users/{uid}", headers=h)
        assert g.status_code == 200
        assert g.json()["userName"] == "bob"

        f = await c.get("/scim/v2/Users", params={"filter": 'userName eq "bob"'}, headers=h)
        assert f.status_code == 200
        lr = f.json()
        assert lr["totalResults"] == 1
        assert lr["Resources"][0]["id"] == uid

        miss = await c.get("/scim/v2/Users", params={"filter": 'userName eq "ghost"'}, headers=h)
        assert miss.json()["totalResults"] == 0


@pytest.mark.asyncio
async def test_patch_active_false_deprovisions(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        uid = (await c.post("/scim/v2/Users", json=_payload("carol"), headers=h)).json()["id"]
        patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        }
        r = await c.patch(f"/scim/v2/Users/{uid}", json=patch, headers=h)
        assert r.status_code == 200
        assert r.json()["active"] is False

        user = await get_user_by_id(pool, uuid.UUID(uid))
        assert user.is_active is False
        status = await pool.fetchval(
            "SELECT status FROM assets WHERE id = $1", user.person_asset_id
        )
        assert status == "archived"


@pytest.mark.asyncio
async def test_delete_deprovisions_softly(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        uid = (await c.post("/scim/v2/Users", json=_payload("dave"), headers=h)).json()["id"]
        r = await c.delete(f"/scim/v2/Users/{uid}", headers=h)
        assert r.status_code == 204

        user = await get_user_by_id(pool, uuid.UUID(uid))
        assert user is not None  # soft delete — the row remains
        assert user.is_active is False
        status = await pool.fetchval(
            "SELECT status FROM assets WHERE id = $1", user.person_asset_id
        )
        assert status == "archived"


@pytest.mark.asyncio
async def test_replace_updates_fields(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        uid = (await c.post("/scim/v2/Users", json=_payload("erin"), headers=h)).json()["id"]
        payload = _payload("erin", display_name="Erin Updated", email="erin2@example.com")
        r = await c.put(f"/scim/v2/Users/{uid}", json=payload, headers=h)
        assert r.status_code == 200
        assert r.json()["displayName"] == "Erin Updated"

        user = await get_user_by_id(pool, uuid.UUID(uid))
        name = await pool.fetchval(
            "SELECT name FROM assets WHERE id = $1", user.person_asset_id
        )
        assert name == "Erin Updated"


@pytest.mark.asyncio
async def test_reactivation_restores_person(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        uid = (await c.post("/scim/v2/Users", json=_payload("frank"), headers=h)).json()["id"]
        # deprovision then re-provision via PUT active=true
        await c.delete(f"/scim/v2/Users/{uid}", headers=h)
        r = await c.put(f"/scim/v2/Users/{uid}", json=_payload("frank", active=True), headers=h)
        assert r.status_code == 200
        assert r.json()["active"] is True
        user = await get_user_by_id(pool, uuid.UUID(uid))
        assert user.is_active is True
        status = await pool.fetchval(
            "SELECT status FROM assets WHERE id = $1", user.person_asset_id
        )
        assert status == "active"


@pytest.mark.asyncio
async def test_service_provider_config(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.get("/scim/v2/ServiceProviderConfig", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200
        assert r.json()["patch"]["supported"] is True
