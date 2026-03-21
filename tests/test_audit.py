"""Tests for audit trail functionality."""

import json

import pytest

from grcen.models.asset import AssetType


# --- Helper to get audit log entries ---

async def _audit_logs(pool, entity_type=None):
    if entity_type:
        rows = await pool.fetch(
            "SELECT * FROM audit_log WHERE entity_type = $1 ORDER BY created_at DESC",
            entity_type,
        )
    else:
        rows = await pool.fetch("SELECT * FROM audit_log ORDER BY created_at DESC")
    return [dict(r) for r in rows]


# --- Asset audit logging ---


@pytest.mark.asyncio
async def test_asset_create_logged(auth_client, pool):
    resp = await auth_client.post(
        "/assets/new",
        data={"type": "person", "name": "Test Person", "description": "desc", "status": "active", "owner": "me"},
    )
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "asset")
    assert len(logs) == 1
    assert logs[0]["action"] == "create"
    assert logs[0]["entity_name"] == "Test Person"
    changes = json.loads(logs[0]["changes"])
    assert "name" in changes


@pytest.mark.asyncio
async def test_asset_update_logged(auth_client, pool):
    from grcen.services import asset as asset_svc

    asset = await asset_svc.create_asset(pool, type=AssetType.PERSON, name="Original", status="active")
    resp = await auth_client.post(
        f"/assets/{asset.id}/edit",
        data={"name": "Updated", "description": "", "status": "active", "owner": ""},
    )
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "asset")
    assert len(logs) == 1
    assert logs[0]["action"] == "update"
    changes = json.loads(logs[0]["changes"])
    assert changes["name"]["old"] == "Original"
    assert changes["name"]["new"] == "Updated"


@pytest.mark.asyncio
async def test_asset_delete_logged(auth_client, pool):
    from grcen.services import asset as asset_svc

    asset = await asset_svc.create_asset(pool, type=AssetType.PERSON, name="DeleteMe", status="active")
    resp = await auth_client.post(f"/assets/{asset.id}/delete")
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "asset")
    assert len(logs) == 1
    assert logs[0]["action"] == "delete"
    assert logs[0]["entity_name"] == "DeleteMe"


# --- User audit logging ---


@pytest.mark.asyncio
async def test_user_create_logged(auth_client, pool):
    resp = await auth_client.post(
        "/admin/users/new",
        data={"username": "audituser", "password": "pass123", "role": "viewer"},
    )
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "user")
    # Filter out login events
    create_logs = [l for l in logs if l["action"] == "create"]
    assert len(create_logs) == 1
    assert create_logs[0]["entity_name"] == "audituser"


@pytest.mark.asyncio
async def test_login_logged(auth_client, pool):
    """Login during auth_client fixture setup should have been logged."""
    logs = await _audit_logs(pool, "user")
    login_logs = [l for l in logs if l["action"] == "login"]
    assert len(login_logs) >= 1


@pytest.mark.asyncio
async def test_user_deactivate_logged(auth_client, pool):
    from grcen.permissions import UserRole
    from grcen.services.auth import create_user

    target = await create_user(pool, "deactivateme", "pass123", role=UserRole.VIEWER)
    resp = await auth_client.post(f"/admin/users/{target.id}/toggle-active")
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "user")
    deact_logs = [l for l in logs if l["action"] == "deactivate"]
    assert len(deact_logs) == 1
    changes = json.loads(deact_logs[0]["changes"])
    assert changes["is_active"]["old"] is True
    assert changes["is_active"]["new"] is False


# --- Audit config ---


@pytest.mark.asyncio
async def test_disable_entity_type_stops_logging(auth_client, pool):
    from grcen.services import audit_service as audit_svc

    # Disable asset logging
    await audit_svc.update_audit_config(pool, "asset", enabled=False, field_level=True)

    resp = await auth_client.post(
        "/assets/new",
        data={"type": "person", "name": "Unlogged", "status": "active", "owner": ""},
    )
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "asset")
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_disable_field_level_strips_changes(auth_client, pool):
    from grcen.services import audit_service as audit_svc

    # Enable logging but disable field-level diffs
    await audit_svc.update_audit_config(pool, "asset", enabled=True, field_level=False)

    resp = await auth_client.post(
        "/assets/new",
        data={"type": "person", "name": "NoFields", "status": "active", "owner": ""},
    )
    assert resp.status_code == 302

    logs = await _audit_logs(pool, "asset")
    assert len(logs) == 1
    assert logs[0]["changes"] is None


# --- Audit log UI access ---


@pytest.mark.asyncio
async def test_admin_can_view_audit_log(auth_client):
    resp = await auth_client.get("/admin/audit")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auditor_can_view_audit_log(auditor_client):
    resp = await auditor_client.get("/admin/audit")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_view_audit_log(viewer_client):
    resp = await viewer_client.get("/admin/audit")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_view_audit_settings(auth_client):
    resp = await auth_client.get("/admin/audit/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auditor_cannot_view_audit_settings(auditor_client):
    resp = await auditor_client.get("/admin/audit/settings")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_update_audit_settings(auth_client, pool):
    resp = await auth_client.post(
        "/admin/audit/settings",
        data={"enabled_asset": "on", "enabled_user": "on"},
    )
    assert resp.status_code == 302

    # Verify: relationship, attachment, alert should now be disabled
    from grcen.services import audit_service as audit_svc
    configs = await audit_svc.get_audit_config_all(pool)
    config_map = {c["entity_type"]: c for c in configs}
    assert config_map["asset"]["enabled"] is True
    assert config_map["relationship"]["enabled"] is False
    assert config_map["alert"]["enabled"] is False
