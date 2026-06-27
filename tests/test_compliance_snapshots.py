"""Compliance posture snapshots: capture, trend, timeline, drift."""
from datetime import date, timedelta

import pytest

from grcen.services import (
    catalog_sync,
    compliance_snapshot_service as css,
    framework_service,
    organization_service,
)

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def _cat(satisfy=False):
    return {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A", "requirements": [
            {"ref": "fwa:R1", "name": "R1"}, {"ref": "fwa:R2", "name": "R2"}]}],
        "controls": ([{"ref": "C1", "name": "C1", "satisfies": ["fwa:R1"]}] if satisfy else []),
    }


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _fid(pool):
    return await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'framework:fwa'")


@pytest.mark.asyncio
async def test_capture_one_row_per_framework_idempotent(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    n = await css.capture_compliance_snapshot(pool, organization_id=org)
    assert n == 1
    await css.capture_compliance_snapshot(pool, organization_id=org)  # same day again
    rows = await pool.fetchval(
        "SELECT count(*) FROM compliance_snapshots WHERE organization_id = $1", org)
    assert rows == 1  # upsert, not duplicate


@pytest.mark.asyncio
async def test_coverage_trend_delta(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(satisfy=False), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=YESTERDAY)  # 0%
    await catalog_sync.sync_catalog(pool, _cat(satisfy=True), organization_id=org)  # live 50%

    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    trends = await css.get_coverage_trends(pool, summaries, organization_id=org)
    fid = str(await _fid(pool))
    assert trends[fid]["current"] == 50
    assert trends[fid]["prior"] == 0
    assert trends[fid]["delta"] == 50
    assert trends[fid]["sparkline"] == [0]  # one prior snapshot in the series


@pytest.mark.asyncio
async def test_timeline_series_oldest_first(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(satisfy=False), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=YESTERDAY)
    await catalog_sync.sync_catalog(pool, _cat(satisfy=True), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=TODAY)

    tl = await css.get_coverage_timeline(pool, await _fid(pool), organization_id=org)
    assert [p["effective_coverage_pct"] for p in tl] == [0, 50]


@pytest.mark.asyncio
async def test_coverage_drift_detected(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(satisfy=True), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=YESTERDAY)  # 50%
    # re-sync without the control prunes the satisfies edge → coverage drops to 0%
    await catalog_sync.sync_catalog(pool, _cat(satisfy=False), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=TODAY)

    drift = await css.coverage_drift(pool, organization_id=org)
    fwa = next(d for d in drift if d["framework_name"] == "FW A")
    assert fwa["from_pct"] == 50 and fwa["to_pct"] == 0


@pytest.mark.asyncio
async def test_capture_all_orgs(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    total = await css.capture_all_org_compliance_snapshots(pool)
    assert total >= 1


@pytest.mark.asyncio
async def test_api_coverage_timeline(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(satisfy=True), organization_id=org)
    await css.capture_compliance_snapshot(pool, organization_id=org, for_date=YESTERDAY)
    fid = await _fid(pool)
    data = (await auth_client.get(f"/api/frameworks/{fid}/coverage-timeline")).json()
    assert len(data) == 1
    assert data[0]["effective_coverage_pct"] == 50
