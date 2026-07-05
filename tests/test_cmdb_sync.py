"""Tests for the CMDB / cloud-inventory sync (roadmap I3)."""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import cmdb_sync, organization_service
from grcen.services.auth import create_user
from grcen.services.token_service import create_token


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _inv(*, systems=None, devices=None, source=None):
    inv = {}
    if systems is not None:
        inv["systems"] = systems
    if devices is not None:
        inv["devices"] = devices
    if source is not None:
        inv["source"] = source
    return inv


def _sys(cid, name, **over):
    s = {"ci_id": cid, "name": name}
    s.update(over)
    return s


async def _asset(pool, org, ref):
    return await pool.fetchrow(
        "SELECT id, type, name, status, metadata, owner_id FROM assets "
        "WHERE organization_id = $1 AND source = 'cmdb' AND source_ref = $2",
        org, ref,
    )


def _meta(row):
    m = row["metadata"]
    return json.loads(m) if isinstance(m, str) else m


# ── service-level ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creates_systems_devices_and_metadata(pool):
    org = await _org(pool)
    result = await cmdb_sync.sync_inventory(
        pool,
        _inv(
            systems=[_sys("sys_a", "Prod Web", hostname="web01", ip_address="10.0.0.5",
                          environment="production", provider="aws", criticality="high")],
            devices=[_sys("dev_x", "Laptop-1", device_type="laptop", serial_number="SN1")],
        ),
        organization_id=org,
    )
    assert not result.errors
    assert result.systems_created == 1
    assert result.devices_created == 1

    sysrow = await _asset(pool, org, "system:sys_a")
    assert sysrow["type"] == "system"
    meta = _meta(sysrow)
    assert meta["hostname"] == "web01"
    assert meta["ip_address"] == "10.0.0.5"
    # external_id defaults to the CI id — this is what lets a uvt finding resolve.
    assert meta["external_id"] == "sys_a"

    devrow = await _asset(pool, org, "device:dev_x")
    assert devrow["type"] == "device"
    assert _meta(devrow)["serial_number"] == "SN1"


@pytest.mark.asyncio
async def test_dependency_edges(pool):
    org = await _org(pool)
    result = await cmdb_sync.sync_inventory(
        pool,
        _inv(systems=[
            _sys("app", "App", depends_on=["db"]),
            _sys("db", "Database"),
        ]),
        organization_id=org,
    )
    assert result.dependency_edges == 1
    edge = await pool.fetchrow(
        "SELECT relationship_type FROM relationships "
        "WHERE source = 'cmdb' AND source_ref = 'depends_on:app:db'"
    )
    assert edge["relationship_type"] == "depends_on"


@pytest.mark.asyncio
async def test_dangling_dependency_warns(pool):
    org = await _org(pool)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("app", "App", depends_on=["ghost"])]),
        organization_id=org,
    )
    assert result.dependency_edges == 0
    assert any("ghost" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_owner_linked_by_name(pool):
    org = await _org(pool)
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Ada Lovelace", organization_id=org)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("sys_a", "Web", owner="Ada Lovelace")]),
        organization_id=org,
    )
    assert result.owners_linked == 1
    sysrow = await _asset(pool, org, "system:sys_a")
    assert sysrow["owner_id"] == person.id


@pytest.mark.asyncio
async def test_unresolved_owner_warns(pool):
    org = await _org(pool)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("sys_a", "Web", owner="Nobody")]),
        organization_id=org,
    )
    assert result.owners_linked == 0
    assert any("Nobody" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_resync_is_idempotent(pool):
    org = await _org(pool)
    inv = _inv(systems=[_sys("a", "A"), _sys("b", "B")])
    await cmdb_sync.sync_inventory(pool, inv, organization_id=org)
    again = await cmdb_sync.sync_inventory(pool, inv, organization_id=org)
    assert again.systems_created == 0
    assert again.systems_updated == 2
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'cmdb' AND organization_id = $1", org
    ) == 2


@pytest.mark.asyncio
async def test_decommissioned_status_archives(pool):
    org = await _org(pool)
    await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A", status="decommissioned")]),
        organization_id=org,
    )
    assert (await _asset(pool, org, "system:a"))["status"] == "archived"


@pytest.mark.asyncio
async def test_missing_ci_archived(pool):
    org = await _org(pool)
    await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A"), _sys("b", "B")]), organization_id=org)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A")]), organization_id=org)
    assert result.systems_archived == 1
    assert "system:b" in result.stale
    assert (await _asset(pool, org, "system:b"))["status"] == "archived"


@pytest.mark.asyncio
async def test_devices_only_sync_does_not_archive_systems(pool):
    org = await _org(pool)
    await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A")]), organization_id=org)
    # A later sync that only carries devices must not touch existing Systems.
    result = await cmdb_sync.sync_inventory(
        pool, _inv(devices=[_sys("d", "Dev")]), organization_id=org)
    assert result.systems_archived == 0
    assert (await _asset(pool, org, "system:a"))["status"] == "active"


@pytest.mark.asyncio
async def test_keep_decommissioned_flag(pool):
    org = await _org(pool)
    await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A"), _sys("b", "B")]), organization_id=org)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A")]), organization_id=org, archive_missing=False)
    assert result.systems_archived == 0
    assert "system:b" in result.stale
    assert (await _asset(pool, org, "system:b"))["status"] == "active"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("a", "A")]), organization_id=org, dry_run=True)
    assert result.dry_run
    assert result.systems_created == 1
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'cmdb'") == 0


@pytest.mark.asyncio
async def test_invalid_inventory_rejected(pool):
    org = await _org(pool)
    # duplicate ci_id across systems + devices
    result = await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("x", "A")], devices=[_sys("x", "B")]),
        organization_id=org,
    )
    assert any("duplicate ci_id" in e for e in result.errors)
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'cmdb'") == 0


@pytest.mark.asyncio
async def test_cross_connector_uvt_resolves_cmdb_system(pool):
    """A CMDB system's external_id (= ci_id) lets a uvt finding link to it."""
    from grcen.services import vuln_sync
    org = await _org(pool)
    await cmdb_sync.sync_inventory(
        pool, _inv(systems=[_sys("sys_a", "Prod Web", hostname="web01")]),
        organization_id=org)
    vres = await vuln_sync.sync_findings(
        pool,
        {"findings": [{"finding_id": "F1", "title": "RCE",
                       "affects": {"external_id": "sys_a"}}]},
        organization_id=org,
    )
    assert vres.affects_edges == 1
    edge = await pool.fetchrow(
        "SELECT a.source_ref FROM relationships r JOIN assets a ON a.id = r.source_asset_id "
        "WHERE r.source = 'uvt' AND r.source_ref = 'affects:F1'")
    assert edge["source_ref"] == "system:sys_a"


# ── HTTP endpoint ────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(pool, f"cmdbadmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "cmdb", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_requires_sync_permission(pool):
    async with _client() as c:
        r = await c.post("/api/sync/cmdb", json=_inv(systems=[_sys("a", "A")]))
        assert r.status_code == 401
        admin = await create_user(pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
        _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)
        r = await c.post(
            "/api/sync/cmdb", json=_inv(systems=[_sys("a", "A")]),
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_http_sync_and_dry_run(pool):
    org = await _org(pool)
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        dr = await c.post(
            "/api/sync/cmdb", params={"dry_run": "true"},
            json=_inv(systems=[_sys("a", "A", hostname="h1")]), headers=h)
        assert dr.status_code == 200
        assert dr.json()["systems_created"] == 1
        assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'cmdb'") == 0

        r = await c.post(
            "/api/sync/cmdb", json=_inv(systems=[_sys("a", "A", hostname="h1")]), headers=h)
        assert r.status_code == 200
        assert r.json()["systems_created"] == 1
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = 'cmdb' AND organization_id = $1", org
        ) == 1


@pytest.mark.asyncio
async def test_http_invalid_inventory_400(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.post(
            "/api/sync/cmdb", json={"systems": "nope"},
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 400
        assert r.json()["errors"]
