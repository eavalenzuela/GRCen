"""Tests for the data access (read) log."""

import io
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import access_log_service, asset as asset_svc
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _asset(pool, admin_id, name="A", asset_type=AssetType.SYSTEM):
    return await asset_svc.create_asset(
        pool, type=asset_type, name=name, status="active", updated_by=admin_id,
    )


# ── record / query helpers ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_writes_row(pool, admin_user):
    await access_log_service.record(
        pool, user=admin_user, action="view",
        entity_type="asset", entity_id=None, entity_name="ping",
        path="/assets", ip_address="127.0.0.1",
    )
    rows = await access_log_service.query(pool)
    assert len(rows) == 1
    assert rows[0]["username"] == admin_user.username
    assert rows[0]["action"] == "view"
    assert rows[0]["entity_name"] == "ping"


@pytest.mark.asyncio
async def test_query_filters(pool, admin_user):
    u2 = await create_user(pool, f"u2_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await access_log_service.record(
        pool, user=admin_user, action="view", entity_type="asset"
    )
    await access_log_service.record(
        pool, user=admin_user, action="download", entity_type="attachment"
    )
    await access_log_service.record(
        pool, user=u2, action="view", entity_type="asset"
    )

    all_rows = await access_log_service.query(pool)
    assert len(all_rows) == 3
    assert len(await access_log_service.query(pool, user_id=admin_user.id)) == 2
    assert len(await access_log_service.query(pool, action="download")) == 1
    assert len(await access_log_service.query(pool, entity_type="attachment")) == 1


# ── route instrumentation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asset_detail_records_view(auth_client, pool, admin_user):
    a = await _asset(pool, admin_user.id, name="Target")
    resp = await auth_client.get(f"/assets/{a.id}")
    assert resp.status_code == 200
    rows = await access_log_service.query(pool)
    matching = [r for r in rows if r["entity_id"] == a.id and r["action"] == "view"]
    assert len(matching) == 1
    assert matching[0]["entity_name"] == "Target"
    assert matching[0]["path"].endswith(f"/assets/{a.id}")


@pytest.mark.asyncio
async def test_asset_pdf_records_pdf_export(auth_client, pool, admin_user):
    a = await _asset(pool, admin_user.id)
    resp = await auth_client.get(f"/assets/{a.id}/report.pdf")
    assert resp.status_code == 200
    rows = await access_log_service.query(pool, action="pdf_export")
    assert any(r["entity_id"] == a.id for r in rows)


@pytest.mark.asyncio
async def test_framework_pdf_records_pdf_export(auth_client, pool, admin_user):
    fw = await _asset(pool, admin_user.id, name="SOC2", asset_type=AssetType.FRAMEWORK)
    resp = await auth_client.get(f"/frameworks/{fw.id}/report.pdf")
    assert resp.status_code == 200
    rows = await access_log_service.query(pool, entity_type="framework")
    assert any(r["entity_id"] == fw.id and r["action"] == "pdf_export" for r in rows)


@pytest.mark.asyncio
async def test_export_records_export(auth_client, pool, admin_user):
    await _asset(pool, admin_user.id)
    resp = await auth_client.get("/api/exports/assets?format=csv")
    assert resp.status_code == 200
    rows = await access_log_service.query(pool, action="export")
    assert len(rows) == 1
    assert "assets.csv" in (rows[0]["entity_name"] or "")


@pytest.mark.asyncio
async def test_attachment_download_records(auth_client, pool, admin_user):
    a = await _asset(pool, admin_user.id)
    upload = await auth_client.post(
        f"/api/assets/{a.id}/attachments/upload",
        files={"file": ("proof.txt", io.BytesIO(b"x"), "text/plain")},
    )
    att_id = upload.json()["id"]
    resp = await auth_client.get(f"/api/assets/{a.id}/attachments/{att_id}/download")
    assert resp.status_code == 200
    rows = await access_log_service.query(pool, action="download")
    assert any(str(r["entity_id"]) == att_id for r in rows)


# ── admin UI + API ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_access_log_page_requires_view_audit(viewer_client):
    resp = await viewer_client.get("/admin/access-log", follow_redirects=False)
    assert resp.status_code in (302, 403)


@pytest.mark.asyncio
async def test_access_log_page_renders_for_admin(auth_client):
    resp = await auth_client.get("/admin/access-log")
    assert resp.status_code == 200
    assert "Data Access Log" in resp.text


@pytest.mark.asyncio
async def test_access_log_api_returns_entries(auth_client, pool, admin_user):
    await access_log_service.record(
        pool, user=admin_user, action="view", entity_type="asset",
        entity_name="target",
    )
    resp = await auth_client.get("/api/access-log/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert any(r["entity_name"] == "target" for r in body)


@pytest.mark.asyncio
async def test_access_log_api_requires_view_audit(editor_client):
    # Editor does NOT have VIEW_AUDIT
    resp = await editor_client.get("/api/access-log/")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_auditor_can_read_access_log(auditor_client):
    resp = await auditor_client.get("/api/access-log/")
    assert resp.status_code == 200
