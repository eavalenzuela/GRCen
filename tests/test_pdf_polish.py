"""Per-framework gap PDF, audit PDF, asset-list PDF + branded covers."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.services import (
    asset as asset_svc,
    framework_service,
    organization_service,
    pdf_service,
    relationship as rel_svc,
)


def _is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF")


@pytest.fixture
async def small_framework(pool):
    fw = await asset_svc.create_asset(pool, type=AssetType.FRAMEWORK, name="ISO-DEMO")
    r1 = await asset_svc.create_asset(pool, type=AssetType.REQUIREMENT, name="ReqA")
    r2 = await asset_svc.create_asset(pool, type=AssetType.REQUIREMENT, name="ReqB")
    c = await asset_svc.create_asset(pool, type=AssetType.CONTROL, name="Ctrl1")
    for r in (r1, r2):
        await rel_svc.create_relationship(
            pool, source_asset_id=fw.id, target_asset_id=r.id, relationship_type="parent_of"
        )
    await rel_svc.create_relationship(
        pool, source_asset_id=c.id, target_asset_id=r1.id, relationship_type="satisfies"
    )
    return {"fw": fw, "r1": r1, "r2": r2, "c": c}


@pytest.mark.asyncio
async def test_render_framework_gap_pdf(pool, small_framework):
    pdf = await pdf_service.render_framework_gap_report(pool, small_framework["fw"].id)
    assert pdf is not None
    assert _is_pdf(pdf)
    # Document content can't be decoded literally, but the size should be > 1KB
    # — the byte stream proves it's a real PDF, not an empty placeholder.
    assert len(pdf) > 1500


@pytest.mark.asyncio
async def test_framework_gap_pdf_404_when_unknown(pool):
    out = await pdf_service.render_framework_gap_report(pool, uuid.uuid4())
    assert out is None


@pytest.mark.asyncio
async def test_render_audit_pdf(pool, small_framework):
    audit = await asset_svc.create_asset(
        pool, type=AssetType.AUDIT, name="2026-Q1 Audit"
    )
    await rel_svc.create_relationship(
        pool, source_asset_id=audit.id, target_asset_id=small_framework["fw"].id,
        relationship_type="certifies",
    )
    pdf = await pdf_service.render_audit_report(pool, audit.id)
    assert pdf is not None
    assert _is_pdf(pdf)


@pytest.mark.asyncio
async def test_audit_pdf_rejects_non_audit_asset(pool, small_framework):
    out = await pdf_service.render_audit_report(pool, small_framework["fw"].id)
    assert out is None


@pytest.mark.asyncio
async def test_render_asset_list_pdf(pool, small_framework):
    pdf = await pdf_service.render_asset_list_report(pool)
    assert _is_pdf(pdf)
    assert len(pdf) > 1500


@pytest.mark.asyncio
async def test_branding_context_uses_org_overrides(pool):
    org = await organization_service.create_organization(
        pool, slug=f"brand_{uuid.uuid4().hex[:6]}", name="BrandCo"
    )
    await organization_service.update_branding(
        pool, org.id, email_from_name="BrandCo Inc",
        email_brand_color="#cc0066", email_logo_url="https://x.test/logo.png",
    )
    ctx = await pdf_service._branding_context(pool, org.id)
    assert ctx["org_name"] == "BrandCo Inc"
    assert ctx["brand_color"] == "#cc0066"
    assert ctx["logo_url"] == "https://x.test/logo.png"


@pytest.mark.asyncio
async def test_branding_context_falls_back_to_defaults(pool):
    ctx = await pdf_service._branding_context(pool, None)
    assert ctx["brand_color"] == "#1e293b"
    assert ctx["logo_url"] == ""
    assert ctx["org_name"] == "GRCen"


@pytest.mark.asyncio
async def test_framework_gap_pdf_endpoint(auth_client, small_framework):
    fw_id = small_framework["fw"].id
    resp = await auth_client.get(f"/frameworks/{fw_id}/gap-report.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_audit_pdf_endpoint(pool, auth_client, small_framework):
    audit = await asset_svc.create_asset(pool, type=AssetType.AUDIT, name="Audit-Q1")
    resp = await auth_client.get(f"/assets/{audit.id}/audit-report.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_audit_pdf_404_for_non_audit(pool, auth_client, small_framework):
    """Asking for an audit PDF on a non-audit asset returns 404."""
    fw_id = small_framework["fw"].id
    resp = await auth_client.get(f"/assets/{fw_id}/audit-report.pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_asset_list_pdf_endpoint(auth_client, small_framework):
    resp = await auth_client.get("/exports/assets.pdf?type=requirement")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
