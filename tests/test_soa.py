"""Statement of Applicability: applicability excludes requirements from coverage."""
import pytest

from grcen.services import catalog_sync, framework_service, organization_service


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _fid(pool):
    return await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'framework:fwa'")


async def _detail(pool, org):
    return await framework_service.get_framework_detail(pool, await _fid(pool), organization_id=org)


def _cat():
    # 2 requirements, R1 satisfied by a control, R2 a gap
    return {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A", "requirements": [
            {"ref": "fwa:R1", "name": "R1"}, {"ref": "fwa:R2", "name": "R2"}]}],
        "controls": [{"ref": "C1", "name": "C1", "satisfies": ["fwa:R1"]}],
    }


async def _set_na(pool, ref):
    """Mark a requirement not applicable (merging metadata)."""
    rid = await pool.fetchval("SELECT id FROM assets WHERE source_ref = $1", f"requirement:{ref}")
    import json
    meta = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", rid)
    meta = json.loads(meta) if isinstance(meta, str) else dict(meta)
    meta["applicable"] = False
    await pool.execute(
        "UPDATE assets SET metadata = $1::jsonb WHERE id = $2", json.dumps(meta), rid)
    return rid


@pytest.mark.asyncio
async def test_default_all_applicable(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    detail = await _detail(pool, org)
    assert detail.applicable_count == 2
    assert detail.not_applicable_count == 0
    assert detail.coverage_percent == 50  # 1 of 2 satisfied


@pytest.mark.asyncio
async def test_not_applicable_excluded_from_denominator(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    await _set_na(pool, "fwa:R2")  # the gap requirement is now out of scope

    detail = await _detail(pool, org)
    assert detail.applicable_count == 1
    assert detail.not_applicable_count == 1
    assert detail.coverage_percent == 100  # only R1 in scope, and it's satisfied
    assert detail.open_gap_count == 0  # R2 no longer counted as a gap


@pytest.mark.asyncio
async def test_summary_excludes_na(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    await _set_na(pool, "fwa:R2")
    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    s = next(x for x in summaries if x.name == "FW A")
    assert s.requirement_count == 1  # denominator is in-scope only
    assert s.coverage_percent == 100


@pytest.mark.asyncio
async def test_soa_page_edit_and_csv(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    fid = await _fid(pool)
    r2 = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwa:R2'")

    page = await auth_client.get(f"/frameworks/{fid}/soa")
    assert page.status_code == 200
    assert "Statement of Applicability" in page.text

    # mark R2 not applicable via the inline edit (no 'applicable' field = unchecked)
    resp = await auth_client.post(
        f"/frameworks/{fid}/soa",
        data={"requirement_id": str(r2), "applicability_justification": "third-party managed",
              "implementation_status": "not_applicable"},
        follow_redirects=False)
    assert resp.status_code == 302

    detail = await _detail(pool, org)
    assert detail.not_applicable_count == 1
    assert detail.coverage_percent == 100
    # the framework/reference_id metadata survived the merge edit
    r2_meta = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", r2)
    import json
    r2_meta = json.loads(r2_meta) if isinstance(r2_meta, str) else r2_meta
    assert r2_meta.get("framework") == "FW A"
    assert r2_meta.get("applicability_justification") == "third-party managed"

    csv = await auth_client.get(f"/frameworks/{fid}/soa.csv")
    assert csv.status_code == 200
    assert "applicable" in csv.text


@pytest.mark.asyncio
async def test_api_exposes_applicability(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _cat(), organization_id=org)
    await _set_na(pool, "fwa:R2")
    detail = (await auth_client.get(f"/api/frameworks/{await _fid(pool)}")).json()
    assert detail["applicable_count"] == 1
    assert detail["not_applicable_count"] == 1
    assert detail["coverage_percent"] == 100
