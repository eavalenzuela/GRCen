"""Configurable approver role per asset type."""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import workflow_service
from grcen.services.auth import create_user
from tests.conftest import login_with_csrf


@pytest_asyncio.fixture
async def admin_and_auditor(pool):
    admin = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    auditor = await create_user(
        pool, f"au_{uuid.uuid4().hex[:8]}", "x", role=UserRole.AUDITOR
    )
    submitter = await create_user(
        pool, f"e_{uuid.uuid4().hex[:8]}", "x", role=UserRole.EDITOR
    )

    async def make_client(u):
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await login_with_csrf(c, u.username, "x")
        return c

    ca = await make_client(admin)
    cau = await make_client(auditor)
    cs = await make_client(submitter)
    try:
        yield {"admin": admin, "auditor": auditor, "submitter": submitter,
               "ca": ca, "cau": cau, "cs": cs}
    finally:
        await ca.aclose()
        await cau.aclose()
        await cs.aclose()


@pytest.mark.asyncio
async def test_no_routing_means_any_approver(admin_and_auditor, pool):
    """Default config (approver_role=None) keeps the original any-APPROVE behaviour."""
    fx = admin_and_auditor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY,
        require_approval_create=True, require_approval_update=False,
        require_approval_delete=False,
    )
    submit = await fx["cs"].post("/api/assets/", json={"type": "policy", "name": "P"})
    cid = submit.json()["pending_change_id"]
    resp = await fx["ca"].post(f"/api/approvals/{cid}/approve", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_role_routing_blocks_non_matching_approver(
    admin_and_auditor, pool
):
    """When approver_role='auditor', an admin can't approve — only auditors can."""
    fx = admin_and_auditor
    await workflow_service.upsert_config(
        pool, AssetType.POLICY,
        require_approval_create=True, require_approval_update=False,
        require_approval_delete=False,
        approver_role="auditor",
    )
    submit = await fx["cs"].post(
        "/api/assets/", json={"type": "policy", "name": "Routed"}
    )
    cid = submit.json()["pending_change_id"]

    # Admin attempt rejected.
    bad = await fx["ca"].post(f"/api/approvals/{cid}/approve", json={})
    assert bad.status_code == 400
    assert "auditor" in bad.text.lower()

    # Auditor attempt — but auditors don't have APPROVE in the role map.
    # Promote auditor to also have APPROVE for this test by upgrading their role.
    # Simpler path: promote them to admin temporarily? No — that would change
    # the role match. Instead, give the auditor APPROVE by making them
    # superadmin (which bypasses the role gate and asserts that property too).
    await pool.execute(
        "UPDATE users SET is_superadmin = true WHERE id = $1", fx["auditor"].id
    )
    # Re-login via session re-fetch — easier: just call the service directly.
    from grcen.services.auth import get_user_by_id
    auditor_user = await get_user_by_id(pool, fx["auditor"].id)
    change = await workflow_service.get(pool, uuid.UUID(cid))
    updated, _ = await workflow_service.approve(pool, change, auditor_user)
    assert updated.status == "approved"


@pytest.mark.asyncio
async def test_admin_form_persists_approver_role(admin_and_auditor, pool):
    fx = admin_and_auditor
    resp = await fx["ca"].post(
        "/admin/workflow",
        data={"approver_role_policy": "auditor"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    cfg = await workflow_service.get_config(pool, AssetType.POLICY)
    assert cfg.approver_role == "auditor"


@pytest.mark.asyncio
async def test_invalid_role_falls_back_to_any(admin_and_auditor, pool):
    fx = admin_and_auditor
    await fx["ca"].post(
        "/admin/workflow",
        data={"approver_role_policy": "junk-role"},
        follow_redirects=False,
    )
    cfg = await workflow_service.get_config(pool, AssetType.POLICY)
    assert cfg.approver_role is None
