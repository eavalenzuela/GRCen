"""Multi-org membership + in-app switcher."""
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
async def two_orgs_one_user(pool):
    """A user that's an admin in org A and a viewer in org B."""
    org_a = await organization_service.create_organization(
        pool, slug=f"a_{uuid.uuid4().hex[:6]}", name="A"
    )
    org_b = await organization_service.create_organization(
        pool, slug=f"b_{uuid.uuid4().hex[:6]}", name="B"
    )
    user = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "x",
        role=UserRole.ADMIN, organization_id=org_a.id,
    )
    await organization_service.add_membership(pool, user.id, org_b.id, "viewer")

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    await login_with_csrf(c, user.username, "x")
    try:
        yield {"org_a": org_a, "org_b": org_b, "user": user, "client": c}
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_create_user_seeds_membership(pool):
    user = await create_user(pool, f"new_{uuid.uuid4().hex[:8]}", "x")
    members = await organization_service.list_memberships(pool, user.id)
    assert len(members) == 1
    assert members[0]["is_default"] is True


@pytest.mark.asyncio
async def test_list_memberships(pool, two_orgs_one_user):
    members = await organization_service.list_memberships(
        pool, two_orgs_one_user["user"].id
    )
    assert len(members) == 2
    by_slug = {m["slug"]: m for m in members}
    assert "admin" in by_slug[two_orgs_one_user["org_a"].slug]["role"]
    assert "viewer" in by_slug[two_orgs_one_user["org_b"].slug]["role"]


@pytest.mark.asyncio
async def test_switch_org_changes_active_tenant(two_orgs_one_user):
    c = two_orgs_one_user["client"]
    org_a = two_orgs_one_user["org_a"]
    org_b = two_orgs_one_user["org_b"]

    # Create an asset in org A (the user's default).
    a_resp = await c.post("/api/assets/", json={"type": "policy", "name": "in-A"})
    assert a_resp.status_code == 201
    a_id = a_resp.json()["id"]

    # Switch to org B and the org-A asset must be invisible.
    switch = await c.post(
        "/switch-org", data={"organization_id": str(org_b.id)},
        follow_redirects=False,
    )
    assert switch.status_code == 302
    list_b = (await c.get("/api/assets/")).json()
    assert all(item["id"] != a_id for item in list_b["items"])

    # Switch back to A and it reappears.
    await c.post(
        "/switch-org", data={"organization_id": str(org_a.id)},
        follow_redirects=False,
    )
    list_a = (await c.get("/api/assets/")).json()
    assert any(item["id"] == a_id for item in list_a["items"])


@pytest.mark.asyncio
async def test_switch_org_uses_per_org_role(two_orgs_one_user):
    """Switching from admin-in-A to viewer-in-B drops write permission."""
    c = two_orgs_one_user["client"]
    org_b = two_orgs_one_user["org_b"]
    await c.post(
        "/switch-org", data={"organization_id": str(org_b.id)},
        follow_redirects=False,
    )
    # As a viewer in B, creating an asset should 403.
    resp = await c.post("/api/assets/", json={"type": "policy", "name": "denied"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_switch_to_non_member_org_rejected(pool, two_orgs_one_user):
    third = await organization_service.create_organization(pool, slug="c", name="C")
    c = two_orgs_one_user["client"]
    resp = await c.post(
        "/switch-org", data={"organization_id": str(third.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "fail" in resp.headers["location"]


@pytest.mark.asyncio
async def test_settings_page_renders_switcher(two_orgs_one_user):
    c = two_orgs_one_user["client"]
    resp = await c.get("/settings")
    assert resp.status_code == 200
    assert "Switch organization" in resp.text


@pytest.mark.asyncio
async def test_remove_membership(pool, two_orgs_one_user):
    user = two_orgs_one_user["user"]
    org_b = two_orgs_one_user["org_b"]
    ok = await organization_service.remove_membership(pool, user.id, org_b.id)
    assert ok is True
    members = await organization_service.list_memberships(pool, user.id)
    assert len(members) == 1
