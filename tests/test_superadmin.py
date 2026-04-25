"""Superadmin role: cross-org admin permission and the /admin/orgs page."""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.permissions import UserRole
from grcen.services import organization_service
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest_asyncio.fixture
async def superadmin_client(pool):
    user = await create_user(
        pool, f"sa_{uuid.uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN
    )
    await pool.execute("UPDATE users SET is_superadmin = true WHERE id = $1", user.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await login_with_csrf(c, user.username, "testpass")
        yield c


@pytest.mark.asyncio
async def test_admin_orgs_page_requires_superadmin(auth_client):
    """Plain admin (not superadmin) gets 403 on /admin/orgs."""
    resp = await auth_client.get("/admin/orgs")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_lists_all_orgs(pool, superadmin_client):
    await organization_service.create_organization(pool, slug="extra", name="Extra Org")
    resp = await superadmin_client.get("/admin/orgs")
    assert resp.status_code == 200
    assert "extra" in resp.text
    assert "default" in resp.text


@pytest.mark.asyncio
async def test_superadmin_creates_org(pool, superadmin_client):
    resp = await superadmin_client.post(
        "/admin/orgs", data={"slug": "newco", "name": "New Co"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    org = await organization_service.get_by_slug(pool, "newco")
    assert org is not None
    assert org.name == "New Co"


@pytest.mark.asyncio
async def test_superadmin_cannot_delete_default_org(pool, superadmin_client):
    org = await organization_service.get_by_slug(pool, "default")
    resp = await superadmin_client.post(f"/admin/orgs/{org.id}/delete")
    assert resp.status_code in (302, 303)
    still_there = await organization_service.get_by_slug(pool, "default")
    assert still_there is not None


@pytest.mark.asyncio
async def test_superadmin_deletes_other_org(pool, superadmin_client):
    org = await organization_service.create_organization(pool, slug="doomed", name="D")
    resp = await superadmin_client.post(f"/admin/orgs/{org.id}/delete")
    assert resp.status_code in (302, 303)
    gone = await organization_service.get_by_id(pool, org.id)
    assert gone is None


@pytest.mark.asyncio
async def test_plain_admin_lacks_manage_orgs_permission(pool, auth_client):
    """Even is_admin=true is not enough — only is_superadmin grants MANAGE_ORGS."""
    resp = await auth_client.post(
        "/admin/orgs", data={"slug": "x", "name": "X"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
