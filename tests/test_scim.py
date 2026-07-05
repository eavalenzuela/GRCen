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
from grcen.services import organization_service, scim_service
from grcen.services.auth import create_user, get_user_by_id
from grcen.services.token_service import create_token


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


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


# ── Groups → role sync (I1) ──────────────────────────────────────────────


async def _member(pool, role=UserRole.VIEWER):
    return await create_user(pool, f"m_{uuid.uuid4().hex[:8]}", "pw", role=role)


@pytest.mark.asyncio
async def test_group_membership_sets_role(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin"})
    u = await _member(pool)
    grp = await scim_service.create_group(
        pool, org, {"displayName": "Admins", "members": [{"value": str(u.id)}]})
    assert grp["displayName"] == "Admins"
    assert any(m["value"] == str(u.id) for m in grp["members"])
    assert (await get_user_by_id(pool, u.id)).role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_highest_privilege_role_wins(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin", "Editors": "editor"})
    u = await _member(pool)
    await scim_service.create_group(
        pool, org, {"displayName": "Editors", "members": [{"value": str(u.id)}]})
    assert (await get_user_by_id(pool, u.id)).role == UserRole.EDITOR
    await scim_service.create_group(
        pool, org, {"displayName": "Admins", "members": [{"value": str(u.id)}]})
    assert (await get_user_by_id(pool, u.id)).role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_patch_add_and_remove_member_recomputes_role(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin"})  # default = viewer
    u = await _member(pool)
    grp = await scim_service.create_group(pool, org, {"displayName": "Admins"})
    gid = uuid.UUID(grp["id"])

    added = await scim_service.patch_group(pool, org, gid, {
        "Operations": [{"op": "add", "path": "members", "value": [{"value": str(u.id)}]}]})
    assert any(m["value"] == str(u.id) for m in added["members"])
    assert (await get_user_by_id(pool, u.id)).role == UserRole.ADMIN

    await scim_service.patch_group(pool, org, gid, {
        "Operations": [{"op": "remove", "path": "members", "value": [{"value": str(u.id)}]}]})
    # removed from the only role-granting group → back to the default (viewer)
    assert (await get_user_by_id(pool, u.id)).role == UserRole.VIEWER


@pytest.mark.asyncio
async def test_patch_remove_via_path_filter(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin"})
    u = await _member(pool)
    grp = await scim_service.create_group(
        pool, org, {"displayName": "Admins", "members": [{"value": str(u.id)}]})
    gid = uuid.UUID(grp["id"])
    await scim_service.patch_group(pool, org, gid, {
        "Operations": [{"op": "remove", "path": f'members[value eq "{u.id}"]'}]})
    g = await scim_service.get_group(pool, org, gid)
    assert not any(m["value"] == str(u.id) for m in g["members"])
    assert (await get_user_by_id(pool, u.id)).role == UserRole.VIEWER


@pytest.mark.asyncio
async def test_put_replaces_members(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin"})
    u1 = await _member(pool)
    u2 = await _member(pool)
    grp = await scim_service.create_group(
        pool, org, {"displayName": "Admins", "members": [{"value": str(u1.id)}]})
    gid = uuid.UUID(grp["id"])
    await scim_service.replace_group(
        pool, org, gid, {"displayName": "Admins", "members": [{"value": str(u2.id)}]})
    assert (await get_user_by_id(pool, u1.id)).role == UserRole.VIEWER   # removed → default
    assert (await get_user_by_id(pool, u2.id)).role == UserRole.ADMIN    # added → admin


@pytest.mark.asyncio
async def test_delete_group_recomputes_members(pool):
    org = await _org(pool)
    await scim_service.set_group_role_map(pool, {"Admins": "admin"})
    u = await _member(pool)
    grp = await scim_service.create_group(
        pool, org, {"displayName": "Admins", "members": [{"value": str(u.id)}]})
    assert (await get_user_by_id(pool, u.id)).role == UserRole.ADMIN
    await scim_service.delete_group(pool, org, uuid.UUID(grp["id"]))
    assert (await get_user_by_id(pool, u.id)).role == UserRole.VIEWER


@pytest.mark.asyncio
async def test_duplicate_group_conflicts(pool):
    org = await _org(pool)
    await scim_service.create_group(pool, org, {"displayName": "Team"})
    with pytest.raises(scim_service.ScimError) as ei:
        await scim_service.create_group(pool, org, {"displayName": "Team"})
    assert ei.value.status == 409


@pytest.mark.asyncio
async def test_http_group_crud_and_filter(pool):
    raw = await _sync_token(pool)
    u = await _member(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        created = await c.post(
            "/scim/v2/Groups",
            json={"displayName": "Engineers", "externalId": "grp-1",
                  "members": [{"value": str(u.id)}]},
            headers=h)
        assert created.status_code == 201, created.text
        gid = created.json()["id"]
        assert created.json()["externalId"] == "grp-1"

        assert (await c.get(f"/scim/v2/Groups/{gid}", headers=h)).status_code == 200

        lr = await c.get("/scim/v2/Groups", params={"filter": 'displayName eq "Engineers"'}, headers=h)
        assert lr.json()["totalResults"] == 1
        assert lr.json()["Resources"][0]["id"] == gid

        assert (await c.delete(f"/scim/v2/Groups/{gid}", headers=h)).status_code == 204
        assert (await c.get(f"/scim/v2/Groups/{gid}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_admin_scim_settings_page_and_save(auth_client, pool):
    from tests.conftest import _extract_csrf_from_session_cookie

    resp = await auth_client.get("/admin/scim-settings")
    assert resp.status_code == 200
    assert "SCIM" in resp.text

    resp = await auth_client.post("/admin/scim-settings", data={
        "default_role": "editor",
        "role_map": '{"GRCen Admins": "admin"}',
        "csrf_token": _extract_csrf_from_session_cookie(auth_client),
    })
    assert resp.status_code in (302, 303)
    assert (await scim_service.get_default_role(pool)).value == "editor"
    assert await scim_service.get_group_role_map(pool) == {"GRCen Admins": "admin"}
