"""Row-level security: the tenant boundary enforced by Postgres, not by discipline.

The suite runs ambiently in system context (see conftest), so these tests enter
``tenant_context`` explicitly to exercise enforced mode.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen import tenancy
from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc, organization_service
from grcen.services.auth import create_user
from grcen.services.token_service import create_token


async def _two_orgs(pool):
    a = await organization_service.get_default_org_id(pool)
    b = (await organization_service.create_organization(
        pool, name="Other", slug=f"rls-{uuid.uuid4().hex[:8]}")).id
    await asset_svc.create_asset(
        pool, organization_id=a, type=AssetType.SYSTEM, name="A-only")
    await asset_svc.create_asset(
        pool, organization_id=b, type=AssetType.SYSTEM, name="B-only")
    return a, b


# ── the mechanism ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforced_mode_runs_as_the_unprivileged_role(pool):
    """Superusers ignore RLS entirely, so dropping privilege is the whole trick."""
    with tenancy.tenant_context(uuid.uuid4()):
        role = await pool.fetchval("SELECT current_user")
    assert role == tenancy.APP_ROLE

    attrs = await pool.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1",
        tenancy.APP_ROLE)
    assert attrs["rolsuper"] is False and attrs["rolbypassrls"] is False


@pytest.mark.asyncio
async def test_system_context_restores_the_owning_role(pool):
    with tenancy.tenant_context(uuid.uuid4()):
        assert await pool.fetchval("SELECT current_user") == tenancy.APP_ROLE
    with tenancy.system_context():
        assert await pool.fetchval("SELECT current_user") != tenancy.APP_ROLE


@pytest.mark.asyncio
async def test_context_does_not_leak_across_connection_release(pool):
    """asyncpg's RESET ALL is load-bearing: a leaked GUC would serve the *wrong*
    tenant's rows, which is worse than no RLS at all."""
    org_a, _ = await _two_orgs(pool)
    with tenancy.tenant_context(org_a):
        assert await pool.fetchval("SELECT count(*) FROM assets") == 1
    # Fresh acquire, no tenant set, still ambient-bypass off for this block.
    with tenancy.tenant_context(None):
        leaked = await pool.fetchval(
            "SELECT current_setting('app.current_org', true)")
    assert leaked in ("", None)


@pytest.mark.asyncio
async def test_every_tenant_table_is_protected_and_forced(pool):
    async with pool.acquire() as conn:
        expected = set(await tenancy.tenant_tables(conn))
        protected = await tenancy.protected_tables(conn)
    assert expected, "no tenant tables discovered — the query is wrong"
    assert expected <= protected
    # The identity plane is deliberately outside RLS; see tenancy.py.
    assert not (tenancy.IDENTITY_TABLES & expected)


# ── isolation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_tenant_select_returns_nothing(pool):
    org_a, org_b = await _two_orgs(pool)
    with tenancy.tenant_context(org_a):
        names = [r["name"] for r in await pool.fetch("SELECT name FROM assets")]
    assert names == ["A-only"]
    with tenancy.tenant_context(org_b):
        names = [r["name"] for r in await pool.fetch("SELECT name FROM assets")]
    assert names == ["B-only"]


@pytest.mark.asyncio
async def test_unfiltered_query_leaks_nothing(pool):
    """The org_views class of bug: a query with no organization_id filter at all."""
    org_a, _ = await _two_orgs(pool)
    with tenancy.tenant_context(org_a):
        rows = await pool.fetch("SELECT name FROM assets")  # no WHERE, on purpose
    assert [r["name"] for r in rows] == ["A-only"]


@pytest.mark.asyncio
async def test_cross_tenant_update_and_delete_affect_nothing(pool):
    org_a, org_b = await _two_orgs(pool)
    with tenancy.tenant_context(org_a):
        await pool.execute("UPDATE assets SET name = 'hacked' WHERE name = 'B-only'")
        await pool.execute("DELETE FROM assets WHERE name = 'B-only'")
    with tenancy.system_context():
        names = sorted(r["name"] for r in await pool.fetch("SELECT name FROM assets"))
    assert names == ["A-only", "B-only"]


@pytest.mark.asyncio
async def test_insert_into_another_tenant_is_refused(pool):
    """WITH CHECK — you can't write a row you wouldn't be allowed to read."""
    org_a, org_b = await _two_orgs(pool)
    with tenancy.tenant_context(org_a):
        with pytest.raises(Exception) as exc:
            await pool.execute(
                "INSERT INTO assets (type, name, organization_id) VALUES ($1,$2,$3)",
                "system", "smuggled", org_b)
    assert "row-level security" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_no_tenant_context_is_fail_closed(pool):
    await _two_orgs(pool)
    with tenancy.tenant_context(None):
        assert await pool.fetchval("SELECT count(*) FROM assets") == 0


@pytest.mark.asyncio
async def test_identity_plane_stays_readable_without_a_tenant(pool):
    """Authentication has to work before a tenant is known."""
    username = f"rls_{uuid.uuid4().hex[:8]}"
    await create_user(pool, username, "pw", role=UserRole.ADMIN)
    with tenancy.tenant_context(None):
        found = await pool.fetchval(
            "SELECT count(*) FROM users WHERE username = $1", username)
    assert found == 1


# ── the request path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticated_requests_drop_to_the_unprivileged_role(pool, monkeypatch):
    """Requests must not inherit the suite's ambient bypass.

    Without this, every other test here could pass while real traffic ran as a
    superuser — policies present, nothing enforced.
    """
    org = await organization_service.get_default_org_id(pool)
    admin = await create_user(
        pool, f"rlsreq_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)

    seen = {}
    real = asset_svc.list_assets

    async def spy(pool_, *a, **kw):
        seen["role"] = await pool_.fetchval("SELECT current_user")
        seen["org"] = await pool_.fetchval(
            "SELECT current_setting('app.current_org', true)")
        return await real(pool_, *a, **kw)

    monkeypatch.setattr(asset_svc, "list_assets", spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/assets/", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert seen["role"] == tenancy.APP_ROLE
    assert seen["org"] == str(org)
