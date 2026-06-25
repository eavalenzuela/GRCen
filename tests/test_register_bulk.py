"""Register framework — Slice 2 (bulk actions).

Covers the generic bulk-update endpoint: direct apply (status / metadata-merge /
tag-append), type + tenant pinning, enum validation, permission gating, the
approval-gated path (K pending_changes), and the list-view bulk fieldset.
"""
import uuid

import pytest

from grcen.models.asset import AssetStatus, AssetType
from grcen.services import asset as asset_svc
from grcen.services import workflow_service


async def _vendor(pool, name, **meta):
    return await asset_svc.create_asset(
        pool, type=AssetType.VENDOR, name=name, metadata_=meta or {},
    )


async def _status(pool, asset_id):
    return await pool.fetchval("SELECT status FROM assets WHERE id = $1", asset_id)


async def _meta(pool, asset_id):
    import json
    m = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", asset_id)
    return json.loads(m) if isinstance(m, str) else (m or {})


# ── direct apply ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_update_status_direct(editor_client, pool):
    a = await _vendor(pool, "Acme")
    b = await _vendor(pool, "Globex")
    c = await _vendor(pool, "Initech")
    resp = await editor_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(a.id), str(b.id)], "status": "archived"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/assets?type=vendor")
    assert await _status(pool, a.id) == AssetStatus.ARCHIVED.value
    assert await _status(pool, b.id) == AssetStatus.ARCHIVED.value
    assert await _status(pool, c.id) == AssetStatus.ACTIVE.value  # unselected untouched


@pytest.mark.asyncio
async def test_bulk_update_merges_metadata_and_appends_tags(editor_client, pool):
    a = await asset_svc.create_asset(
        pool, type=AssetType.VENDOR, name="Acme",
        metadata_={"tier": "low", "contract_end": "2030-01-01"}, tags=["existing"],
    )
    resp = await editor_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(a.id)], "meta.tier": "high", "add_tags": "soc2, existing"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    meta = await _meta(pool, a.id)
    assert meta["tier"] == "high"            # changed
    assert meta["contract_end"] == "2030-01-01"  # other keys preserved
    tags = await pool.fetchval("SELECT tags FROM assets WHERE id = $1", a.id)
    assert set(tags) == {"existing", "soc2"}  # appended + de-duped


@pytest.mark.asyncio
async def test_bulk_update_is_type_pinned(editor_client, pool):
    """An id of another type passed to the vendor endpoint is not touched."""
    sys_asset = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Sys")
    resp = await editor_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(sys_asset.id)], "status": "archived"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert await _status(pool, sys_asset.id) == AssetStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_bulk_update_rejects_invalid_enum(editor_client, pool):
    a = await _vendor(pool, "Acme", tier="low")
    await editor_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(a.id)], "meta.tier": "not-a-real-tier"},
        follow_redirects=False,
    )
    assert (await _meta(pool, a.id))["tier"] == "low"  # unchanged


@pytest.mark.asyncio
async def test_viewer_cannot_bulk_update(viewer_client, pool):
    a = await _vendor(pool, "Acme")
    resp = await viewer_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(a.id)], "status": "archived"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert await _status(pool, a.id) == AssetStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_bulk_update_unknown_type_400(editor_client):
    resp = await editor_client.post(
        "/assets/bulk-update?type=answer",  # posture type — no register/bulk
        data={"asset_ids": [str(uuid.uuid4())], "status": "archived"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# ── approval-gated path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_update_gated_creates_pending_changes(editor_client, pool):
    await workflow_service.upsert_config(
        pool, AssetType.VENDOR,
        require_approval_create=False, require_approval_update=True,
        require_approval_delete=False,
    )
    a = await _vendor(pool, "Acme")
    b = await _vendor(pool, "Globex")
    resp = await editor_client.post(
        "/assets/bulk-update?type=vendor",
        data={"asset_ids": [str(a.id), str(b.id)], "status": "archived"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/approvals")
    # Assets unchanged until approval…
    assert await _status(pool, a.id) == AssetStatus.ACTIVE.value
    # …and one pending change per selected asset.
    pending = await pool.fetchval(
        "SELECT count(*) FROM pending_changes WHERE action = 'update' AND asset_type = 'vendor' AND status = 'pending'"
    )
    assert pending == 2


# ── list-view fieldset ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_shows_bulk_fieldset_for_editor_not_viewer(editor_client, viewer_client, pool):
    await _vendor(pool, "Acme")
    editor = await editor_client.get("/assets?type=vendor")
    assert "Bulk Apply to Selected" in editor.text
    assert 'name="asset_ids"' in editor.text

    viewer = await viewer_client.get("/assets?type=vendor")
    assert "Bulk Apply to Selected" not in viewer.text
