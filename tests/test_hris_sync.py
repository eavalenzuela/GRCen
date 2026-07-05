"""Tests for the HRIS roster sync (roadmap I2)."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services import hris_sync, organization_service
from grcen.services.auth import (
    create_user,
    get_user_by_id,
    set_person_asset_link,
)
from grcen.services.token_service import create_token


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _roster(*employees, org_units=None, source=None):
    r = {"employees": list(employees)}
    if org_units is not None:
        r["org_units"] = org_units
    if source is not None:
        r["source"] = source
    return r


def _emp(eid, name, **over):
    e = {"employee_id": eid, "name": name}
    e.update(over)
    return e


async def _person_ref(pool, org, ref):
    return await pool.fetchrow(
        "SELECT id, status, metadata FROM assets "
        "WHERE organization_id = $1 AND source = 'hris' AND source_ref = $2",
        org, ref,
    )


# ── service-level ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creates_people_ou_and_edges(pool):
    org = await _org(pool)
    roster = _roster(
        _emp("E0", "Grace Hopper"),
        _emp("E1", "Ada Lovelace", manager_employee_id="E0", org_unit_ref="eng",
             title="Engineer"),
        org_units=[
            {"ref": "company", "name": "Acme"},
            {"ref": "eng", "name": "Engineering", "parent_ref": "company"},
        ],
    )
    result = await hris_sync.sync_roster(pool, roster, organization_id=org)
    assert not result.errors
    assert result.persons_created == 2
    assert result.org_units_created == 2
    assert result.manager_edges == 1
    assert result.membership_edges == 1
    assert result.hierarchy_edges == 1
    assert result.warnings == []

    # Person carries the mapped metadata + employee_id.
    ada = await _person_ref(pool, org, "emp:E1")
    import json
    meta = ada["metadata"]
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["employee_id"] == "E1"
    assert meta["title"] == "Engineer"

    # manages edge Grace(E0) -> Ada(E1)
    edge = await pool.fetchrow(
        "SELECT relationship_type FROM relationships "
        "WHERE source = 'hris' AND source_ref = 'manages:E0:E1'"
    )
    assert edge["relationship_type"] == "manages"


@pytest.mark.asyncio
async def test_resync_is_idempotent(pool):
    org = await _org(pool)
    roster = _roster(_emp("E0", "Grace"), _emp("E1", "Ada", manager_employee_id="E0"))
    await hris_sync.sync_roster(pool, roster, organization_id=org)
    again = await hris_sync.sync_roster(pool, roster, organization_id=org)
    assert again.persons_created == 0
    assert again.persons_updated == 2
    assert again.edges_pruned == 0
    total = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'hris' AND organization_id = $1", org
    )
    assert total == 2


@pytest.mark.asyncio
async def test_mover_reparents_manager_edge(pool):
    org = await _org(pool)
    await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace"), _emp("E1", "Ada", manager_employee_id="E0")),
        organization_id=org,
    )
    # Ada now reports to E2 instead of E0.
    moved = await hris_sync.sync_roster(
        pool,
        _roster(
            _emp("E0", "Grace"), _emp("E2", "Alan"),
            _emp("E1", "Ada", manager_employee_id="E2"),
        ),
        organization_id=org,
    )
    assert moved.edges_pruned == 1  # old manages:E0:E1 gone
    assert await pool.fetchval(
        "SELECT count(*) FROM relationships WHERE source_ref = 'manages:E0:E1'"
    ) == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM relationships WHERE source_ref = 'manages:E2:E1'"
    ) == 1


@pytest.mark.asyncio
async def test_leaver_archived_and_login_disabled(pool):
    org = await _org(pool)
    await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace"), _emp("E1", "Ada")), organization_id=org
    )
    # Link a login account to Ada's Person.
    ada = await _person_ref(pool, org, "emp:E1")
    user = await create_user(pool, f"ada_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await set_person_asset_link(pool, user.id, ada["id"])

    # Re-sync without Ada — she's a leaver.
    result = await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace")), organization_id=org
    )
    assert result.leavers == ["emp:E1"]
    assert result.persons_archived == 1
    assert result.users_deactivated == 1

    ada = await _person_ref(pool, org, "emp:E1")
    assert ada["status"] == "archived"
    refreshed = await get_user_by_id(pool, user.id)
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_leaver_kept_when_flag_false(pool):
    org = await _org(pool)
    await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace"), _emp("E1", "Ada")), organization_id=org
    )
    result = await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace")),
        organization_id=org, deactivate_leavers=False,
    )
    assert result.leavers == ["emp:E1"]
    assert result.persons_archived == 0
    ada = await _person_ref(pool, org, "emp:E1")
    assert ada["status"] == "active"  # reported but untouched


@pytest.mark.asyncio
async def test_reassign_owned_assets_on_leave(pool):
    org = await _org(pool)
    await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace"), _emp("E1", "Ada")), organization_id=org
    )
    ada = await _person_ref(pool, org, "emp:E1")
    grace = await _person_ref(pool, org, "emp:E0")
    # A system owned by Ada.
    sys_id = await pool.fetchval(
        "INSERT INTO assets (type, name, owner_id, organization_id) "
        "VALUES ('system', 'Prod', $1, $2) RETURNING id",
        ada["id"], org,
    )
    result = await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace")),
        organization_id=org, reassign_owned_to=grace["id"],
    )
    assert result.assets_reassigned == 1
    new_owner = await pool.fetchval("SELECT owner_id FROM assets WHERE id = $1", sys_id)
    assert new_owner == grace["id"]


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    result = await hris_sync.sync_roster(
        pool, _roster(_emp("E0", "Grace"), _emp("E1", "Ada")),
        organization_id=org, dry_run=True,
    )
    assert result.dry_run
    assert result.persons_created == 2  # counts reflect a real run
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'hris'") == 0


@pytest.mark.asyncio
async def test_dangling_manager_warns_not_fails(pool):
    org = await _org(pool)
    result = await hris_sync.sync_roster(
        pool, _roster(_emp("E1", "Ada", manager_employee_id="GHOST")),
        organization_id=org,
    )
    assert not result.errors
    assert result.persons_created == 1
    assert result.manager_edges == 0
    assert any("GHOST" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_invalid_roster_rejected(pool):
    org = await _org(pool)
    result = await hris_sync.sync_roster(
        pool, {"employees": [{"name": "no id"}]}, organization_id=org
    )
    assert result.errors
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'hris'") == 0


@pytest.mark.asyncio
async def test_inactive_employee_provisions_archived(pool):
    org = await _org(pool)
    await hris_sync.sync_roster(
        pool, _roster(_emp("E9", "Departing Dan", active=False)), organization_id=org
    )
    dan = await _person_ref(pool, org, "emp:E9")
    assert dan["status"] == "archived"


# ── HTTP endpoint ────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(pool, f"hradmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "hris", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_requires_sync_permission(pool):
    async with _client() as c:
        r = await c.post("/api/sync/hris", json=_roster(_emp("E1", "Ada")))
        assert r.status_code == 401
        admin = await create_user(pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
        _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)
        r = await c.post(
            "/api/sync/hris", json=_roster(_emp("E1", "Ada")),
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_http_sync_and_dry_run(pool):
    org = await _org(pool)
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        # dry-run writes nothing
        dr = await c.post(
            "/api/sync/hris", params={"dry_run": "true"},
            json=_roster(_emp("E0", "Grace"), _emp("E1", "Ada", manager_employee_id="E0")),
            headers=h,
        )
        assert dr.status_code == 200
        assert dr.json()["persons_created"] == 2
        assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'hris'") == 0

        # real run persists
        r = await c.post(
            "/api/sync/hris",
            json=_roster(_emp("E0", "Grace"), _emp("E1", "Ada", manager_employee_id="E0")),
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["persons_created"] == 2
        assert body["manager_edges"] == 1
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = 'hris' AND organization_id = $1", org
        ) == 2


@pytest.mark.asyncio
async def test_http_invalid_roster_400(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.post(
            "/api/sync/hris", json={"employees": [{"name": "no id"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 400
        assert r.json()["errors"]
