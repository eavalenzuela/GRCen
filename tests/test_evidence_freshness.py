"""Evidence freshness engine + freshness-gated coverage."""
from datetime import UTC, datetime, timedelta

import pytest

from grcen.models.attachment import AttachmentKind
from grcen.services import (
    attachment as attach_svc,
    catalog_sync,
    evidence_service,
    framework_service,
    organization_service,
)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _cat():
    return {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A",
                        "requirements": [{"ref": "fwa:R1", "name": "R1"}]}],
        "controls": [{"ref": "C1", "name": "C1",
                      "metadata": {"effectiveness": "effective"}, "satisfies": ["fwa:R1"]}],
    }


async def _ctrl_id(pool):
    return await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'control:C1'")


async def _fid(pool):
    return await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'framework:fwa'")


async def _detail(pool, org):
    return await framework_service.get_framework_detail(
        pool, await _fid(pool), organization_id=org)


async def _attach(pool, org, asset_id, valid_until):
    return await attach_svc.create_attachment(
        pool, organization_id=org, asset_id=asset_id, kind=AttachmentKind.URL,
        name="evidence", url_or_path="http://e", valid_until=valid_until)


def test_classify():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    assert evidence_service.classify(None, now=now) == "untracked"
    assert evidence_service.classify(now - timedelta(days=1), now=now) == "expired"
    assert evidence_service.classify(now + timedelta(days=10), now=now) == "aging"
    assert evidence_service.classify(now + timedelta(days=200), now=now) == "fresh"


@pytest.mark.asyncio
async def test_create_attachment_stores_validity(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    cid = await _ctrl_id(pool)
    vu = datetime.now(UTC) + timedelta(days=200)
    att = await _attach(pool, org, cid, vu)
    assert att.valid_until is not None
    assert att.collected_at is not None  # defaulted to now()


@pytest.mark.asyncio
async def test_evidence_status_for_assets_worst(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    cid = await _ctrl_id(pool)
    await _attach(pool, org, cid, datetime.now(UTC) + timedelta(days=200))  # fresh
    await _attach(pool, org, cid, datetime.now(UTC) - timedelta(days=5))    # expired
    statuses = await evidence_service.evidence_status_for_assets(pool, [cid])
    assert statuses[cid] == "expired"  # worst wins


@pytest.mark.asyncio
async def test_gated_coverage_marks_stale(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    await _attach(pool, org, await _ctrl_id(pool), datetime.now(UTC) - timedelta(days=5))

    detail = await _detail(pool, org)
    r1 = detail.requirements[0]
    assert r1.satisfied is True
    assert r1.evidence_status == "expired"
    assert r1.stale_evidence is True
    assert detail.stale_evidence_count == 1
    assert detail.evidence_freshness_percent == 0


@pytest.mark.asyncio
async def test_fresh_evidence_is_not_stale(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    await _attach(pool, org, await _ctrl_id(pool), datetime.now(UTC) + timedelta(days=200))
    detail = await _detail(pool, org)
    assert detail.requirements[0].evidence_status == "fresh"
    assert detail.stale_evidence_count == 0
    assert detail.evidence_freshness_percent == 100


@pytest.mark.asyncio
async def test_list_stale_and_expiring(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    cid = await _ctrl_id(pool)
    await _attach(pool, org, cid, datetime.now(UTC) - timedelta(days=5))    # expired
    await _attach(pool, org, cid, datetime.now(UTC) + timedelta(days=200))  # fresh (excluded)
    stale = await evidence_service.list_stale_evidence(pool, organization_id=org)
    assert len(stale) == 1 and stale[0]["status"] == "expired"
    expiring = await evidence_service.expiring_evidence(pool, organization_id=org)
    assert len(expiring) == 1


@pytest.mark.asyncio
async def test_evidence_page_and_api(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    cid = await _ctrl_id(pool)
    await _attach(pool, org, cid, datetime.now(UTC) - timedelta(days=5))

    page = await auth_client.get("/evidence")
    assert page.status_code == 200
    assert "expired" in page.text

    detail = (await auth_client.get(f"/api/frameworks/{await _fid(pool)}")).json()
    assert detail["stale_evidence_count"] == 1
    assert detail["evidence_freshness_percent"] == 0
    assert detail["requirements"][0]["evidence_status"] == "expired"
