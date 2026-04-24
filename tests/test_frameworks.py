"""Tests for framework dashboards: coverage computation, pages, and API."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import framework_service, relationship as rel_svc
from grcen.services.auth import create_user


async def _mk_asset(pool, admin_id, name, asset_type, **kwargs):
    return await asset_svc.create_asset(
        pool,
        type=asset_type,
        name=name,
        status="active",
        updated_by=admin_id,
        **kwargs,
    )


async def _link(pool, source, target, rel_type, description=""):
    return await rel_svc.create_relationship(
        pool,
        source_asset_id=source.id,
        target_asset_id=target.id,
        relationship_type=rel_type,
        description=description,
    )


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


# ── coverage computation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_framework_has_zero_coverage(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "SOC2", AssetType.FRAMEWORK)

    summaries = await framework_service.list_frameworks(pool)
    assert len(summaries) == 1
    assert summaries[0].requirement_count == 0
    assert summaries[0].satisfied_count == 0
    assert summaries[0].coverage_percent == 0

    detail = await framework_service.get_framework_detail(pool, fw.id)
    assert detail is not None
    assert detail.requirements == []
    assert detail.gap_count == 0
    assert detail.coverage_percent == 0


@pytest.mark.asyncio
async def test_requirement_satisfied_by_policy_counts(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "SOC2", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "CC6.1", AssetType.REQUIREMENT)
    policy = await _mk_asset(pool, admin_user.id, "Access Policy", AssetType.POLICY)

    await _link(pool, fw, req, "parent_of")
    await _link(pool, req, policy, "satisfied_by")

    detail = await framework_service.get_framework_detail(pool, fw.id)
    assert detail.coverage_percent == 100
    assert detail.satisfied_count == 1
    assert len(detail.requirements[0].satisfiers) == 1
    assert detail.requirements[0].satisfiers[0]["via"] == "satisfied_by"


@pytest.mark.asyncio
async def test_requirement_satisfied_by_control_via_inbound_edge(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "PCI", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "Req3", AssetType.REQUIREMENT)
    control = await _mk_asset(pool, admin_user.id, "Encryption", AssetType.CONTROL)

    await _link(pool, fw, req, "parent_of")
    await _link(pool, control, req, "satisfies")  # inbound edge

    detail = await framework_service.get_framework_detail(pool, fw.id)
    assert detail.coverage_percent == 100
    assert detail.requirements[0].satisfiers[0]["type"] == "control"


@pytest.mark.asyncio
async def test_mixed_coverage(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "GDPR", AssetType.FRAMEWORK)
    req1 = await _mk_asset(pool, admin_user.id, "Art 17", AssetType.REQUIREMENT)
    req2 = await _mk_asset(pool, admin_user.id, "Art 32", AssetType.REQUIREMENT)
    req3 = await _mk_asset(pool, admin_user.id, "Art 33", AssetType.REQUIREMENT)
    policy = await _mk_asset(pool, admin_user.id, "DR Policy", AssetType.POLICY)
    system = await _mk_asset(pool, admin_user.id, "Vault", AssetType.SYSTEM)

    await _link(pool, fw, req1, "parent_of")
    await _link(pool, fw, req2, "parent_of")
    await _link(pool, fw, req3, "parent_of")
    await _link(pool, req1, policy, "satisfied_by")
    await _link(pool, req2, system, "implemented_by")
    # req3 intentionally left uncovered

    detail = await framework_service.get_framework_detail(pool, fw.id)
    assert detail.satisfied_count == 2
    assert detail.gap_count == 1
    assert detail.coverage_percent == 67


@pytest.mark.asyncio
async def test_detail_returns_audits_and_vendors(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "SOC2", AssetType.FRAMEWORK)
    audit = await _mk_asset(pool, admin_user.id, "SOC2 Audit 2026", AssetType.AUDIT)
    vendor = await _mk_asset(pool, admin_user.id, "Okta", AssetType.VENDOR)

    await _link(pool, fw, audit, "certifies")
    await _link(pool, vendor, fw, "certified_by")

    detail = await framework_service.get_framework_detail(pool, fw.id)
    assert [a["name"] for a in detail.audits] == ["SOC2 Audit 2026"]
    assert [v["name"] for v in detail.vendors] == ["Okta"]


@pytest.mark.asyncio
async def test_in_scope_assets_excludes_requirements_and_frameworks(pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "F", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "R", AssetType.REQUIREMENT)
    system = await _mk_asset(pool, admin_user.id, "S", AssetType.SYSTEM)
    policy = await _mk_asset(pool, admin_user.id, "P", AssetType.POLICY)

    await _link(pool, fw, req, "parent_of")
    await _link(pool, req, system, "implemented_by")
    await _link(pool, req, policy, "satisfied_by")

    detail = await framework_service.get_framework_detail(pool, fw.id)
    names = sorted(a["name"] for a in detail.in_scope_assets)
    assert names == ["P", "S"]  # requirement and framework excluded


# ── page routes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frameworks_index_page_renders(auth_client, pool):
    # With no frameworks the page should still render
    resp = await auth_client.get("/frameworks")
    assert resp.status_code == 200
    assert "Compliance Frameworks" in resp.text


@pytest.mark.asyncio
async def test_framework_detail_page_404_for_unknown(auth_client):
    resp = await auth_client.get(f"/frameworks/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_framework_detail_page_renders(auth_client, pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "SOC2", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "CC6.1", AssetType.REQUIREMENT)
    await _link(pool, fw, req, "parent_of")

    resp = await auth_client.get(f"/frameworks/{fw.id}")
    assert resp.status_code == 200
    assert "SOC2" in resp.text
    assert "CC6.1" in resp.text
    assert "gap" in resp.text  # unsatisfied


# ── API endpoints ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_list_frameworks(auth_client, pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "ISO27001", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "A.8.1", AssetType.REQUIREMENT)
    policy = await _mk_asset(pool, admin_user.id, "Inv Policy", AssetType.POLICY)
    await _link(pool, fw, req, "parent_of")
    await _link(pool, req, policy, "satisfied_by")

    resp = await auth_client.get("/api/frameworks/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "ISO27001"
    assert body[0]["coverage_percent"] == 100
    assert body[0]["requirement_count"] == 1
    assert body[0]["id"] == str(fw.id)


@pytest.mark.asyncio
async def test_api_get_framework(auth_client, pool, admin_user):
    fw = await _mk_asset(pool, admin_user.id, "GDPR", AssetType.FRAMEWORK)
    req = await _mk_asset(pool, admin_user.id, "Art 17", AssetType.REQUIREMENT)
    await _link(pool, fw, req, "parent_of")

    resp = await auth_client.get(f"/api/frameworks/{fw.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["framework"]["name"] == "GDPR"
    assert body["gap_count"] == 1
    assert body["satisfied_count"] == 0
    assert len(body["requirements"]) == 1
    assert body["requirements"][0]["satisfied"] is False


@pytest.mark.asyncio
async def test_api_framework_not_found(auth_client):
    resp = await auth_client.get(f"/api/frameworks/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── RBAC ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frameworks_index_requires_auth(client):
    resp = await client.get("/frameworks", follow_redirects=False)
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_viewer_can_access_frameworks(viewer_client):
    resp = await viewer_client.get("/frameworks")
    assert resp.status_code == 200
