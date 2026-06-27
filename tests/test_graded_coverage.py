"""Effectiveness-weighted (graded) coverage: a control's health weighted into coverage."""
import pytest

from grcen.models.asset import AssetType
from grcen.services import (
    asset as asset_svc,
    catalog_sync,
    framework_service,
    organization_service,
    relationship as rel_svc,
)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _fid(pool):
    return await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'framework:fwa'")


async def _detail(pool, org):
    return await framework_service.get_framework_detail(
        pool, await _fid(pool), organization_id=org)


def _cat_one(eff):
    return {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A",
                        "requirements": [{"ref": "fwa:R1", "name": "R1"}]}],
        "controls": [{"ref": "C1", "name": "C1",
                      "metadata": {"effectiveness": eff}, "satisfies": ["fwa:R1"]}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("eff,strength,graded", [
    ("effective", 1.0, "satisfied_strong"),
    ("partially_effective", 0.5, "satisfied_weak"),
    ("ineffective", 0.0, "satisfied_weak"),
    ("not_tested", 0.25, "satisfied_weak"),
])
async def test_requirement_grading(pool, eff, strength, graded):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat_one(eff), organization_id=org)
    detail = await _detail(pool, org)
    r1 = detail.requirements[0]
    assert r1.satisfied is True
    assert r1.satisfaction_strength == strength
    assert r1.graded == graded


@pytest.mark.asyncio
async def test_health_adjusted_coverage(pool):
    org = await _org(pool)
    cat = {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A", "requirements": [
            {"ref": "fwa:R1", "name": "R1"}, {"ref": "fwa:R2", "name": "R2"}]}],
        "controls": [
            {"ref": "C1", "name": "C1",
             "metadata": {"effectiveness": "effective"}, "satisfies": ["fwa:R1"]},
            {"ref": "C2", "name": "C2",
             "metadata": {"effectiveness": "ineffective"}, "satisfies": ["fwa:R2"]},
        ],
    }
    await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    detail = await _detail(pool, org)
    assert detail.coverage_percent == 100  # both satisfied on paper
    assert detail.health_adjusted_coverage_percent == 50  # (1.0 + 0.0) / 2
    assert detail.weak_count == 1


@pytest.mark.asyncio
async def test_summary_exposes_graded(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat_one("partially_effective"), organization_id=org)
    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    s = next(x for x in summaries if x.name == "FW A")
    assert s.coverage_percent == 100
    assert s.health_adjusted_coverage_percent == 50
    assert s.weak_count == 1


@pytest.mark.asyncio
async def test_non_control_satisfier_is_ungraded(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A",
                        "requirements": [{"ref": "fwa:R1", "name": "R1"}]}],
        "controls": [],
    }, organization_id=org)
    req_id = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwa:R1'")
    policy = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.POLICY, name="Access Policy")
    await rel_svc.create_relationship(
        pool, organization_id=org, source_asset_id=req_id,
        target_asset_id=policy.id, relationship_type="satisfied_by")

    detail = await _detail(pool, org)
    r1 = detail.requirements[0]
    assert r1.satisfied is True
    assert r1.satisfaction_strength is None  # not backed by a control
    assert r1.graded == "satisfied"  # ungraded, counts fully
    assert detail.health_adjusted_coverage_percent == 100


@pytest.mark.asyncio
async def test_api_exposes_graded_fields(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat_one("ineffective"), organization_id=org)
    fid = await _fid(pool)

    lst = (await auth_client.get("/api/frameworks/")).json()
    s = next(f for f in lst if f["name"] == "FW A")
    assert s["weak_count"] == 1
    assert s["health_adjusted_coverage_percent"] == 0

    detail = (await auth_client.get(f"/api/frameworks/{fid}")).json()
    assert detail["health_adjusted_coverage_percent"] == 0
    assert detail["weak_count"] == 1
    assert detail["requirements"][0]["graded"] == "satisfied_weak"
    assert detail["requirements"][0]["satisfaction_strength"] == 0.0
