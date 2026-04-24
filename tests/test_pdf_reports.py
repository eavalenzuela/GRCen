"""Tests for PDF report generation."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import pdf_service
from grcen.services import relationship as rel_svc
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _mk(pool, admin_id, name, asset_type, **kwargs):
    return await asset_svc.create_asset(
        pool, type=asset_type, name=name, status="active",
        updated_by=admin_id, **kwargs,
    )


# ── service-level ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_framework_pdf_renders(pool, admin_user):
    fw = await _mk(pool, admin_user.id, "SOC 2", AssetType.FRAMEWORK,
                    metadata_={"version": "2017", "certification_status": "certified"})
    req = await _mk(pool, admin_user.id, "CC6.1", AssetType.REQUIREMENT)
    await rel_svc.create_relationship(
        pool, source_asset_id=fw.id, target_asset_id=req.id,
        relationship_type="parent_of", description="",
    )
    pdf = await pdf_service.render_framework_report(pool, fw.id)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_framework_pdf_returns_none_for_unknown(pool):
    pdf = await pdf_service.render_framework_report(pool, uuid.uuid4())
    assert pdf is None


@pytest.mark.asyncio
async def test_asset_pdf_renders(pool, admin_user):
    a = await _mk(pool, admin_user.id, "App", AssetType.SYSTEM,
                  metadata_={"stack": "python"})
    other = await _mk(pool, admin_user.id, "DB", AssetType.SYSTEM)
    await rel_svc.create_relationship(
        pool, source_asset_id=a.id, target_asset_id=other.id,
        relationship_type="depends_on", description="needs db",
    )
    pdf = await pdf_service.render_asset_report(pool, a.id)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")
    # Non-trivial content size suggests it actually rendered the body.
    assert len(pdf) > 1500


@pytest.mark.asyncio
async def test_asset_pdf_returns_none_for_unknown(pool):
    pdf = await pdf_service.render_asset_report(pool, uuid.uuid4())
    assert pdf is None


# ── HTTP routes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_framework_report_route(auth_client, pool, admin_user):
    fw = await _mk(pool, admin_user.id, "ISO", AssetType.FRAMEWORK)
    resp = await auth_client.get(f"/frameworks/{fw.id}/report.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment;" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_framework_report_route_404(auth_client):
    resp = await auth_client.get(f"/frameworks/{uuid.uuid4()}/report.pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_asset_report_route(auth_client, pool, admin_user):
    a = await _mk(pool, admin_user.id, "MyApp", AssetType.SYSTEM)
    resp = await auth_client.get(f"/assets/{a.id}/report.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_asset_report_route_404(auth_client):
    resp = await auth_client.get(f"/assets/{uuid.uuid4()}/report.pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_framework_report_requires_auth(client):
    resp = await client.get(
        f"/frameworks/{uuid.uuid4()}/report.pdf", follow_redirects=False
    )
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_viewer_can_download_pdf(viewer_client, pool):
    admin = await create_user(
        pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )
    a = await _mk(pool, admin.id, "Sys", AssetType.SYSTEM)
    resp = await viewer_client.get(f"/assets/{a.id}/report.pdf")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")
