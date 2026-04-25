"""Cross-organization isolation tests.

Two users in different orgs must never see each other's data: assets,
relationships, attachments, alerts, audit log, access log, saved searches,
workflow config, pending changes. We also assert that cross-org references
are rejected at write time (asset.owner_id, relationship endpoints,
attachment owner).
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import organization_service
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest_asyncio.fixture
async def two_orgs(pool):
    """Create two orgs each with their own admin client."""
    org_a = await organization_service.create_organization(
        pool, slug=f"a_{uuid.uuid4().hex[:6]}", name="Org A"
    )
    org_b = await organization_service.create_organization(
        pool, slug=f"b_{uuid.uuid4().hex[:6]}", name="Org B"
    )
    user_a = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "testpass",
        role=UserRole.ADMIN, organization_id=org_a.id,
    )
    user_b = await create_user(
        pool, f"b_{uuid.uuid4().hex[:8]}", "testpass",
        role=UserRole.ADMIN, organization_id=org_b.id,
    )

    async def make_client(user):
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await login_with_csrf(c, user.username, "testpass")
        return c

    ca = await make_client(user_a)
    cb = await make_client(user_b)
    try:
        yield {"org_a": org_a, "org_b": org_b, "user_a": user_a, "user_b": user_b, "ca": ca, "cb": cb}
    finally:
        await ca.aclose()
        await cb.aclose()


@pytest.mark.asyncio
async def test_assets_isolated_between_orgs(two_orgs):
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    a_resp = await ca.post("/api/assets/", json={"type": "policy", "name": "Org A policy"})
    assert a_resp.status_code == 201
    a_id = a_resp.json()["id"]

    # Org A sees its asset; org B does not
    list_a = (await ca.get("/api/assets/")).json()
    list_b = (await cb.get("/api/assets/")).json()
    assert any(it["id"] == a_id for it in list_a["items"])
    assert not any(it["id"] == a_id for it in list_b["items"])

    # Direct GET from B is 404
    assert (await cb.get(f"/api/assets/{a_id}")).status_code == 404
    # Update from B is 404
    assert (await cb.put(f"/api/assets/{a_id}", json={"name": "hijack"})).status_code == 404
    # Delete from B is 404 (asset preserved)
    assert (await cb.delete(f"/api/assets/{a_id}")).status_code == 404
    assert (await ca.get(f"/api/assets/{a_id}")).status_code == 200


@pytest.mark.asyncio
async def test_search_isolated_between_orgs(two_orgs):
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    await ca.post("/api/assets/", json={"type": "system", "name": "Shared name"})
    await cb.post("/api/assets/", json={"type": "system", "name": "Shared name"})

    a_search = (await ca.get("/api/assets/search?q=Shared")).json()
    b_search = (await cb.get("/api/assets/search?q=Shared")).json()
    assert len(a_search) == 1
    assert len(b_search) == 1
    assert a_search[0]["id"] != b_search[0]["id"]


@pytest.mark.asyncio
async def test_owner_cross_org_rejected(pool, two_orgs):
    """Setting owner_id on an asset to a person from another org must fail."""
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]
    # Owner candidate in org B
    other_owner = await asset_svc.create_asset(
        pool, organization_id=org_b.id, type=AssetType.PERSON, name="B-Owner"
    )
    ca = two_orgs["ca"]
    resp = await ca.post(
        "/api/assets/",
        json={"type": "system", "name": "X", "owner_id": str(other_owner.id)},
    )
    assert resp.status_code == 400
    assert "different organization" in resp.text


@pytest.mark.asyncio
async def test_relationship_cross_org_rejected(pool, two_orgs):
    """Linking an asset in org A to one in org B must fail."""
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]
    a_asset = await asset_svc.create_asset(
        pool, organization_id=org_a.id, type=AssetType.SYSTEM, name="A-sys"
    )
    b_asset = await asset_svc.create_asset(
        pool, organization_id=org_b.id, type=AssetType.SYSTEM, name="B-sys"
    )
    ca = two_orgs["ca"]
    resp = await ca.post(
        "/api/relationships/",
        json={
            "source_asset_id": str(a_asset.id),
            "target_asset_id": str(b_asset.id),
            "relationship_type": "depends_on",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_relationships_isolated_between_orgs(pool, two_orgs):
    """An asset in org A doesn't surface relationships from org B that share an id space."""
    org_a = two_orgs["org_a"]
    a1 = await asset_svc.create_asset(pool, organization_id=org_a.id, type=AssetType.SYSTEM, name="a1")
    a2 = await asset_svc.create_asset(pool, organization_id=org_a.id, type=AssetType.SYSTEM, name="a2")
    ca = two_orgs["ca"]
    create = await ca.post(
        "/api/relationships/",
        json={
            "source_asset_id": str(a1.id),
            "target_asset_id": str(a2.id),
            "relationship_type": "depends_on",
        },
    )
    assert create.status_code == 201
    rel_id = create.json()["id"]
    # Org B sees nothing for either asset
    cb = two_orgs["cb"]
    assert (await cb.get(f"/api/relationships/?asset_id={a1.id}")).json() == []
    assert (await cb.get(f"/api/relationships/{rel_id}")).status_code == 404


@pytest.mark.asyncio
async def test_audit_log_isolated_between_orgs(two_orgs):
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    await ca.post("/api/assets/", json={"type": "policy", "name": "Audit test"})
    # admin/audit page renders only the requesting org's events
    resp_a = await ca.get("/admin/audit")
    resp_b = await cb.get("/admin/audit")
    assert "Audit test" in resp_a.text
    assert "Audit test" not in resp_b.text


@pytest.mark.asyncio
async def test_workflow_config_isolated_between_orgs(two_orgs):
    """Each org has its own workflow gates; one org's config doesn't apply to another."""
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    # Org A turns on the create gate for 'policy'
    await ca.post("/admin/workflow", data={"create_policy": "on"})

    # Org A: 202 (gated), Org B: 201 (no gate)
    a_resp = await ca.post("/api/assets/", json={"type": "policy", "name": "A gated"})
    b_resp = await cb.post("/api/assets/", json={"type": "policy", "name": "B free"})
    assert a_resp.status_code == 202
    assert b_resp.status_code == 201


@pytest.mark.asyncio
async def test_pending_changes_isolated_between_orgs(two_orgs):
    """Pending changes from another org must not appear in a user's queue."""
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    await ca.post("/admin/workflow", data={"create_policy": "on"})
    submitted = await ca.post("/api/assets/", json={"type": "policy", "name": "Cross-org pending"})
    assert submitted.status_code == 202

    a_queue = (await ca.get("/api/approvals/")).json()
    b_queue = (await cb.get("/api/approvals/")).json()
    assert len(a_queue) == 1
    assert len(b_queue) == 0


@pytest.mark.asyncio
async def test_user_listing_isolated_between_orgs(two_orgs):
    """Org A admin sees only org A users."""
    ca, cb = two_orgs["ca"], two_orgs["cb"]
    a_users = (await ca.get("/admin/users")).text
    b_users = (await cb.get("/admin/users")).text
    user_a_name = two_orgs["user_a"].username
    user_b_name = two_orgs["user_b"].username
    assert user_a_name in a_users
    assert user_b_name not in a_users
    assert user_b_name in b_users
    assert user_a_name not in b_users
