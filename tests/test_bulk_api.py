"""Tests for bulk JSON-body API endpoints and dry-run semantics."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user
from grcen.services.import_service import (
    execute_asset_import,
    execute_relationship_import,
    preview_relationship_import,
)


# ── service-level dry-run ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_asset_import_dry_run_writes_nothing(pool):
    content = '[{"name":"A","type":"system","status":"active"}]'
    result = await execute_asset_import(pool, content, "json", dry_run=True)
    assert result.created == 1
    assert result.errors == []
    count = await pool.fetchval("SELECT count(*) FROM assets")
    assert count == 0


@pytest.mark.asyncio
async def test_execute_asset_import_wet_writes(pool):
    content = '[{"name":"A","type":"system","status":"active"}]'
    await execute_asset_import(pool, content, "json", dry_run=False)
    count = await pool.fetchval("SELECT count(*) FROM assets WHERE name='A'")
    assert count == 1


@pytest.mark.asyncio
async def test_preview_relationship_import_flags_missing_assets(pool):
    # Source exists, target does not
    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Src", status="active", updated_by=admin.id
    )
    content = (
        '[{"source_name":"Src","source_type":"system",'
        '"target_name":"Missing","target_type":"system",'
        '"relationship_type":"depends_on"}]'
    )
    preview = await preview_relationship_import(pool, content, "json")
    assert preview.total_rows == 1
    assert preview.valid_rows == 0
    assert any("Missing" in e for e in preview.errors)


@pytest.mark.asyncio
async def test_preview_relationship_import_valid_row(pool):
    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="S1", status="active", updated_by=admin.id
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="S2", status="active", updated_by=admin.id
    )
    content = (
        '[{"source_name":"S1","source_type":"system",'
        '"target_name":"S2","target_type":"system",'
        '"relationship_type":"depends_on"}]'
    )
    preview = await preview_relationship_import(pool, content, "json")
    assert preview.total_rows == 1
    assert preview.valid_rows == 1
    assert preview.errors == []


@pytest.mark.asyncio
async def test_execute_relationship_import_dry_run_writes_nothing(pool):
    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="S1", status="active", updated_by=admin.id
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="S2", status="active", updated_by=admin.id
    )
    content = (
        '[{"source_name":"S1","source_type":"system",'
        '"target_name":"S2","target_type":"system",'
        '"relationship_type":"depends_on"}]'
    )
    result = await execute_relationship_import(pool, content, "json", dry_run=True)
    assert result.created == 1
    count = await pool.fetchval("SELECT count(*) FROM relationships")
    assert count == 0


# ── HTTP endpoints ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_assets_endpoint(auth_client, pool):
    payload = [
        {"name": "Sys A", "type": "system", "status": "active"},
        {"name": "Sys B", "type": "system", "status": "active"},
    ]
    resp = await auth_client.post("/api/imports/assets/bulk", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert body["errors"] == []
    assert body["dry_run"] is False
    count = await pool.fetchval("SELECT count(*) FROM assets")
    assert count == 2


@pytest.mark.asyncio
async def test_bulk_assets_dry_run_does_not_write(auth_client, pool):
    payload = [{"name": "Dry", "type": "system", "status": "active"}]
    resp = await auth_client.post(
        "/api/imports/assets/bulk?dry_run=true", json=payload
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["dry_run"] is True
    count = await pool.fetchval("SELECT count(*) FROM assets")
    assert count == 0


@pytest.mark.asyncio
async def test_bulk_assets_returns_errors_for_invalid_rows(auth_client):
    payload = [
        {"name": "ok", "type": "system"},
        {"type": "system"},  # missing name
        {"name": "bad", "type": "not_a_type"},
    ]
    resp = await auth_client.post("/api/imports/assets/bulk", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 2


@pytest.mark.asyncio
async def test_bulk_relationships_endpoint(auth_client, pool):
    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Alpha", status="active", updated_by=admin.id
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Beta", status="active", updated_by=admin.id
    )
    payload = [
        {
            "source_name": "Alpha",
            "source_type": "system",
            "target_name": "Beta",
            "target_type": "system",
            "relationship_type": "depends_on",
            "description": "wires",
        }
    ]
    resp = await auth_client.post("/api/imports/relationships/bulk", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["errors"] == []
    count = await pool.fetchval("SELECT count(*) FROM relationships")
    assert count == 1


@pytest.mark.asyncio
async def test_bulk_endpoint_requires_import_permission(viewer_client):
    resp = await viewer_client.post(
        "/api/imports/assets/bulk",
        json=[{"name": "x", "type": "system"}],
    )
    assert resp.status_code in (401, 403)


# ── Bearer token auth against bulk + core API ────────────────────────────


@pytest.mark.asyncio
async def test_bearer_token_can_bulk_insert(client, pool):
    """An API token with IMPORT permission should be able to POST to /bulk."""
    from grcen.services import token_service

    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    _token, raw = await token_service.create_token(
        pool, user_id=admin.id, name="bulk-test", permissions=["import", "view"]
    )

    resp = await client.post(
        "/api/imports/assets/bulk",
        json=[{"name": "TokenCreated", "type": "system", "status": "active"}],
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1


@pytest.mark.asyncio
async def test_bearer_token_without_import_perm_rejected(client, pool):
    from grcen.services import token_service

    admin = await create_user(
        pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    _token, raw = await token_service.create_token(
        pool, user_id=admin.id, name="view-only", permissions=["view"]
    )

    resp = await client.post(
        "/api/imports/assets/bulk",
        json=[{"name": "x", "type": "system"}],
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403
