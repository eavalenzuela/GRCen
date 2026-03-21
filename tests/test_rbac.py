"""Integration tests for RBAC enforcement across API endpoints."""

import pytest


# --- Helper to create an asset via admin client ---

async def _create_asset(client, name="Test Asset"):
    resp = await client.post(
        "/api/assets/",
        json={"type": "policy", "name": name, "status": "active"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# --- Asset CRUD ---


@pytest.mark.asyncio
async def test_viewer_cannot_create_asset(viewer_client):
    resp = await viewer_client.post(
        "/api/assets/",
        json={"type": "policy", "name": "Blocked", "status": "active"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_assets(auth_client, viewer_client):
    asset_id = await _create_asset(auth_client)
    resp = await viewer_client.get(f"/api/assets/{asset_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_update_asset(auth_client, viewer_client):
    asset_id = await _create_asset(auth_client)
    resp = await viewer_client.put(f"/api/assets/{asset_id}", json={"name": "Renamed"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_delete_asset(auth_client, viewer_client):
    asset_id = await _create_asset(auth_client)
    resp = await viewer_client.delete(f"/api/assets/{asset_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_create_asset(editor_client):
    resp = await editor_client.post(
        "/api/assets/",
        json={"type": "policy", "name": "Editor Asset", "status": "active"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_editor_can_update_asset(editor_client):
    asset_id = await _create_asset(editor_client)
    resp = await editor_client.put(f"/api/assets/{asset_id}", json={"name": "Updated"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_editor_can_delete_asset(editor_client):
    asset_id = await _create_asset(editor_client)
    resp = await editor_client.delete(f"/api/assets/{asset_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_auditor_cannot_create_asset(auditor_client):
    resp = await auditor_client.post(
        "/api/assets/",
        json={"type": "policy", "name": "Blocked", "status": "active"},
    )
    assert resp.status_code == 403


# --- Import / Export ---


@pytest.mark.asyncio
async def test_viewer_cannot_import(viewer_client):
    csv = "name,type,status\nTest,policy,active"
    resp = await viewer_client.post(
        "/api/imports/assets/preview",
        files={"file": ("assets.csv", csv, "text/csv")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_export(viewer_client):
    resp = await viewer_client.get("/api/exports/assets?format=csv")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_auditor_can_export(auditor_client):
    resp = await auditor_client.get("/api/exports/assets?format=csv")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auditor_cannot_import(auditor_client):
    csv = "name,type,status\nTest,policy,active"
    resp = await auditor_client.post(
        "/api/imports/assets/preview",
        files={"file": ("assets.csv", csv, "text/csv")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_import(editor_client):
    csv = "name,type,status\nTest,policy,active"
    resp = await editor_client.post(
        "/api/imports/assets/preview",
        files={"file": ("assets.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200


# --- Alerts ---


@pytest.mark.asyncio
async def test_viewer_cannot_create_alert(auth_client, viewer_client):
    asset_id = await _create_asset(auth_client)
    resp = await viewer_client.post(
        "/api/alerts/",
        json={
            "asset_id": asset_id,
            "title": "Review",
            "schedule_type": "once",
            "next_fire_at": "2026-12-01T00:00:00Z",
            "enabled": True,
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read_alerts(auth_client, viewer_client):
    resp = await viewer_client.get("/api/alerts/")
    assert resp.status_code == 200


# --- Graph ---


@pytest.mark.asyncio
async def test_viewer_can_view_graph(auth_client, viewer_client):
    asset_id = await _create_asset(auth_client)
    resp = await viewer_client.get(f"/api/graph/{asset_id}")
    assert resp.status_code == 200


# --- updated_by tracking ---


@pytest.mark.asyncio
async def test_create_asset_sets_updated_by(auth_client):
    resp = await auth_client.post(
        "/api/assets/",
        json={"type": "policy", "name": "Tracked", "status": "active"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["updated_by"] is not None


@pytest.mark.asyncio
async def test_update_asset_sets_updated_by(auth_client):
    asset_id = await _create_asset(auth_client)
    resp = await auth_client.put(f"/api/assets/{asset_id}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["updated_by"] is not None
