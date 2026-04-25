"""Workflow extended to relationship create/delete actions."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc, workflow_service


@pytest.mark.asyncio
async def test_relationship_create_without_gate_is_immediate(auth_client, pool):
    """No gate set → creating an edge still goes through 201."""
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="A")
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="B")
    resp = await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": str(a.id),
            "target_asset_id": str(b.id),
            "relationship_type": "depends_on",
        },
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_relationship_create_with_gate_returns_202(auth_client, pool):
    """Gate on system → 202 + pending change instead of an edge row."""
    await workflow_service.upsert_config(
        pool, AssetType.SYSTEM,
        require_approval_create=False, require_approval_update=False,
        require_approval_delete=False,
        require_approval_relationship_create=True,
        require_approval_relationship_delete=False,
    )
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="A")
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="B")
    resp = await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": str(a.id),
            "target_asset_id": str(b.id),
            "relationship_type": "depends_on",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "relationship_create"
    assert "pending_change_id" in body
    # No edge row written yet.
    rels = await pool.fetchval("SELECT count(*) FROM relationships")
    assert rels == 0


@pytest.mark.asyncio
async def test_approve_relationship_create_writes_edge(pool):
    """Approving a queued relationship_create lays the edge down."""
    from grcen.services.auth import create_user
    from grcen.permissions import UserRole
    submitter = await create_user(
        pool, f"s_{uuid.uuid4().hex[:8]}", "x", role=UserRole.EDITOR
    )
    approver = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN
    )
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="A")
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="B")
    change = await workflow_service.submit(
        pool, action="relationship_create", asset_type=AssetType.SYSTEM,
        target_asset_id=a.id, title="link",
        payload={
            "source_asset_id": str(a.id),
            "target_asset_id": str(b.id),
            "relationship_type": "depends_on",
            "description": None,
        },
        user=submitter,
    )
    updated, _ = await workflow_service.approve(pool, change, approver)
    assert updated.status == "approved"
    rels = await pool.fetchval(
        "SELECT count(*) FROM relationships WHERE source_asset_id = $1", a.id
    )
    assert rels == 1


@pytest.mark.asyncio
async def test_relationship_delete_with_gate_returns_202(auth_client, pool):
    """Gate on relationship_delete keeps the edge until approval."""
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="A")
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="B")
    create_resp = await auth_client.post(
        "/api/relationships/",
        json={
            "source_asset_id": str(a.id),
            "target_asset_id": str(b.id),
            "relationship_type": "depends_on",
        },
    )
    rel_id = create_resp.json()["id"]
    # Now turn the gate on for delete.
    await workflow_service.upsert_config(
        pool, AssetType.SYSTEM,
        require_approval_create=False, require_approval_update=False,
        require_approval_delete=False,
        require_approval_relationship_create=False,
        require_approval_relationship_delete=True,
    )
    delete = await auth_client.delete(f"/api/relationships/{rel_id}")
    assert delete.status_code == 202
    # Edge row still exists.
    still = await pool.fetchval(
        "SELECT 1 FROM relationships WHERE id = $1", uuid.UUID(rel_id)
    )
    assert still == 1


@pytest.mark.asyncio
async def test_admin_workflow_form_persists_relationship_gates(auth_client, pool):
    resp = await auth_client.post(
        "/admin/workflow",
        data={
            "rel_create_system": "on",
            "rel_delete_system": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    cfg = await workflow_service.get_config(pool, AssetType.SYSTEM)
    assert cfg.require_approval_relationship_create is True
    assert cfg.require_approval_relationship_delete is True
