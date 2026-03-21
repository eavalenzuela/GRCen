"""Tests for asset cloning."""

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc
from grcen.services import relationship as rel_svc


@pytest.mark.asyncio
async def test_clone_asset_basic(pool):
    original = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Original Person", status="active",
        owner="HR", description="A person", metadata_={"title": "Engineer"},
    )
    clone = await asset_svc.clone_asset(pool, original.id)
    assert clone is not None
    assert clone.id != original.id
    assert clone.name == "Original Person (Copy)"
    assert clone.type == original.type
    assert clone.status == original.status
    assert clone.owner == original.owner
    assert clone.description == original.description
    assert clone.metadata_.get("title") == "Engineer"


@pytest.mark.asyncio
async def test_clone_asset_custom_name(pool):
    original = await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="Data Policy", status="active",
    )
    clone = await asset_svc.clone_asset(pool, original.id, new_name="Data Policy v2")
    assert clone.name == "Data Policy v2"


@pytest.mark.asyncio
async def test_clone_asset_without_relationships(pool):
    a1 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys A", status="active")
    a2 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys B", status="active")
    await rel_svc.create_relationship(
        pool, source_asset_id=a1.id, target_asset_id=a2.id,
        relationship_type="connects_to",
    )

    clone = await asset_svc.clone_asset(pool, a1.id, clone_relationships=False)
    clone_rels = await rel_svc.list_relationships_for_asset(pool, clone.id)
    assert len(clone_rels) == 0


@pytest.mark.asyncio
async def test_clone_asset_with_relationships(pool):
    a1 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys A", status="active")
    a2 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys B", status="active")
    a3 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys C", status="active")
    # a1 -> a2 (outgoing)
    await rel_svc.create_relationship(
        pool, source_asset_id=a1.id, target_asset_id=a2.id,
        relationship_type="connects_to",
    )
    # a3 -> a1 (incoming)
    await rel_svc.create_relationship(
        pool, source_asset_id=a3.id, target_asset_id=a1.id,
        relationship_type="depends_on",
    )

    clone = await asset_svc.clone_asset(pool, a1.id, clone_relationships=True)
    clone_rels = await rel_svc.list_relationships_for_asset(pool, clone.id)
    assert len(clone_rels) == 2

    # Check outgoing: clone -> a2
    outgoing = [r for r in clone_rels if r.source_asset_id == clone.id]
    assert len(outgoing) == 1
    assert outgoing[0].target_asset_id == a2.id
    assert outgoing[0].relationship_type == "connects_to"

    # Check incoming: a3 -> clone
    incoming = [r for r in clone_rels if r.target_asset_id == clone.id]
    assert len(incoming) == 1
    assert incoming[0].source_asset_id == a3.id
    assert incoming[0].relationship_type == "depends_on"


@pytest.mark.asyncio
async def test_clone_nonexistent_asset(pool):
    import uuid
    clone = await asset_svc.clone_asset(pool, uuid.uuid4())
    assert clone is None


@pytest.mark.asyncio
async def test_clone_via_page(auth_client, pool):
    original = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Clone Me", status="active",
    )
    resp = await auth_client.post(f"/assets/{original.id}/clone")
    assert resp.status_code == 302

    # Verify clone exists
    row = await pool.fetchrow("SELECT * FROM assets WHERE name = 'Clone Me (Copy)'")
    assert row is not None


@pytest.mark.asyncio
async def test_clone_via_page_with_relationships(auth_client, pool):
    a1 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="CloneSys", status="active")
    a2 = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Other", status="active")
    await rel_svc.create_relationship(
        pool, source_asset_id=a1.id, target_asset_id=a2.id,
        relationship_type="links_to",
    )

    resp = await auth_client.post(
        f"/assets/{a1.id}/clone",
        data={"clone_relationships": "on"},
    )
    assert resp.status_code == 302

    row = await pool.fetchrow("SELECT id FROM assets WHERE name = 'CloneSys (Copy)'")
    assert row is not None
    rels = await pool.fetch(
        "SELECT * FROM relationships WHERE source_asset_id = $1 OR target_asset_id = $1",
        row["id"],
    )
    assert len(rels) == 1


@pytest.mark.asyncio
async def test_clone_audit_logged(auth_client, pool):
    original = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Audit Clone", status="active",
    )
    await auth_client.post(f"/assets/{original.id}/clone")

    logs = await pool.fetch(
        "SELECT * FROM audit_log WHERE action = 'clone' AND entity_type = 'asset'"
    )
    assert len(logs) == 1
    assert str(original.id) in logs[0]["changes"]


@pytest.mark.asyncio
async def test_viewer_cannot_clone(viewer_client, pool):
    original = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="No Clone", status="active",
    )
    resp = await viewer_client.post(f"/assets/{original.id}/clone")
    assert resp.status_code == 403
