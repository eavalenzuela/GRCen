"""Borrowed coverage via crosswalks + the cross-framework matrix."""
import pytest

from grcen.services import catalog_sync, framework_service, organization_service


def _catalog(rel="equivalent", satisfy_a=True):
    """Framework A's A1 (optionally satisfied by a control) cross-maps to B's B1."""
    return {
        "catalog_version": "1",
        "source": "autocomply",
        "frameworks": [
            {"ref": "fwa", "name": "Framework A",
             "requirements": [{"ref": "fwa:A1", "name": "A1 — alpha", "reference_id": "A1"}]},
            {"ref": "fwb", "name": "Framework B",
             "requirements": [{"ref": "fwb:B1", "name": "B1 — beta", "reference_id": "B1"}]},
        ],
        "controls": [
            {"ref": "C1", "name": "Control 1", "satisfies": (["fwa:A1"] if satisfy_a else [])},
        ],
        "crosswalks": [
            {"from": "fwa:A1", "to": "fwb:B1", "relationship": rel, "confidence": "high"},
        ],
    }


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _fw_id(pool, ref):
    return await pool.fetchval(
        "SELECT id FROM assets WHERE source_ref = $1", f"framework:{ref}"
    )


async def _detail(pool, ref, org):
    return await framework_service.get_framework_detail(
        pool, await _fw_id(pool, ref), organization_id=org
    )


@pytest.mark.asyncio
async def test_gap_borrows_coverage_from_equivalent_satisfied_requirement(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    # B1 is a *direct* gap (no control satisfies it) but A1 (equivalent) is satisfied.
    detail = await _detail(pool, "fwb", org)
    b1 = detail.requirements[0]
    assert b1.satisfied is False
    assert b1.covered_via_crosswalk is True
    assert b1.coverage == "covered_via_crosswalk"
    assert [b["code"] for b in b1.borrowed_from] == ["A1"]
    assert b1.borrowed_from[0]["framework"] == "Framework A"

    assert detail.coverage_percent == 0  # nothing directly satisfied
    assert detail.borrowed_count == 1
    assert detail.effective_coverage_percent == 100
    assert detail.open_gap_count == 0


@pytest.mark.asyncio
async def test_only_equivalent_crosswalks_lend_coverage(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(rel="partial"), organization_id=org)
    detail = await _detail(pool, "fwb", org)
    b1 = detail.requirements[0]
    assert b1.coverage == "gap"  # partial overlap doesn't imply the gap is met
    assert detail.borrowed_count == 0
    assert detail.open_gap_count == 1


@pytest.mark.asyncio
async def test_no_borrow_when_equivalent_is_itself_a_gap(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(satisfy_a=False), organization_id=org)
    detail = await _detail(pool, "fwb", org)
    assert detail.requirements[0].coverage == "gap"
    assert detail.borrowed_count == 0


@pytest.mark.asyncio
async def test_summary_reports_effective_coverage(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)
    fws = await framework_service.list_frameworks(pool, organization_id=org)
    summaries = {s.name: s for s in fws}
    b = summaries["Framework B"]
    assert b.satisfied_count == 0
    assert b.borrowed_count == 1
    assert b.coverage_percent == 0
    assert b.effective_coverage_percent == 100
    a = summaries["Framework A"]
    assert a.satisfied_count == 1  # directly satisfied; doesn't borrow


@pytest.mark.asyncio
async def test_crosswalk_matrix_counts_pairs_symmetrically(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)
    m = await framework_service.crosswalk_matrix(pool, organization_id=org)
    assert m["total"] == 1
    fa = await _fw_id(pool, "fwa")
    fb = await _fw_id(pool, "fwb")
    assert m["matrix"][str(fa)][str(fb)] == 1
    assert m["matrix"][str(fb)][str(fa)] == 1  # symmetric


@pytest.mark.asyncio
async def test_intra_framework_equivalent_does_not_borrow(pool):
    """A same-framework equivalent must not lend coverage, and index==detail."""
    org = await _org(pool)
    cat = {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "Framework A", "requirements": [
            {"ref": "fwa:A1", "name": "A1", "reference_id": "A1"},
            {"ref": "fwa:A2", "name": "A2", "reference_id": "A2"}]}],
        "controls": [{"ref": "C1", "name": "C1", "satisfies": ["fwa:A1"]}],
        "crosswalks": [{"from": "fwa:A1", "to": "fwa:A2", "relationship": "equivalent"}],
    }
    await catalog_sync.sync_catalog(pool, cat, organization_id=org)

    fws = await framework_service.list_frameworks(pool, organization_id=org)
    a = next(s for s in fws if s.name == "Framework A")
    assert a.borrowed_count == 0  # summary must not borrow within a framework
    detail = await _detail(pool, "fwa", org)
    assert detail.borrowed_count == 0  # and must agree with the index
    assert next(r for r in detail.requirements if r.name == "A2").coverage == "gap"


@pytest.mark.asyncio
async def test_matrix_dedups_duplicate_edges(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)
    a1 = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwa:A1'")
    b1 = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwb:B1'")
    # A human-authored reverse cross_maps edge for the same pair must not double-count.
    await pool.execute(
        """INSERT INTO relationships (source_asset_id, target_asset_id,
               relationship_type, organization_id)
           VALUES ($1, $2, 'cross_maps', $3)""",
        b1, a1, org,
    )
    m = await framework_service.crosswalk_matrix(pool, organization_id=org)
    assert m["total"] == 1


@pytest.mark.asyncio
async def test_borrowing_is_case_insensitive(pool):
    """A human-authored 'Equivalent' (capitalised) edge still lends coverage."""
    org = await _org(pool)
    cat = {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [
            {"ref": "fwa", "name": "Framework A",
             "requirements": [{"ref": "fwa:A1", "name": "A1", "reference_id": "A1"}]},
            {"ref": "fwb", "name": "Framework B",
             "requirements": [{"ref": "fwb:B1", "name": "B1", "reference_id": "B1"}]},
        ],
        "controls": [{"ref": "C1", "name": "C1", "satisfies": ["fwa:A1"]}],
    }
    await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    a1 = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwa:A1'")
    b1 = await pool.fetchval("SELECT id FROM assets WHERE source_ref = 'requirement:fwb:B1'")
    await pool.execute(
        """INSERT INTO relationships (source_asset_id, target_asset_id,
               relationship_type, description, organization_id)
           VALUES ($1, $2, 'cross_maps', 'Equivalent · confidence: high', $3)""",
        a1, b1, org,
    )
    detail = await _detail(pool, "fwb", org)
    assert detail.requirements[0].coverage == "covered_via_crosswalk"


@pytest.mark.asyncio
async def test_gap_report_rows_carry_coverage_tier(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)
    fb = await _fw_id(pool, "fwb")
    rows = await framework_service.gap_report_rows(pool, fb, organization_id=org)
    assert rows[0]["coverage"] == "covered_via_crosswalk"
    assert "A1" in rows[0]["borrowed_from"]


@pytest.mark.asyncio
async def test_api_exposes_coverage_tiers_and_matrix(auth_client, pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    lst = (await auth_client.get("/api/frameworks/")).json()
    b = next(f for f in lst if f["name"] == "Framework B")
    assert b["coverage_percent"] == 0
    assert b["effective_coverage_percent"] == 100
    assert b["borrowed_count"] == 1

    fb = await _fw_id(pool, "fwb")
    detail = (await auth_client.get(f"/api/frameworks/{fb}")).json()
    assert detail["effective_coverage_percent"] == 100
    assert detail["borrowed_count"] == 1
    req = detail["requirements"][0]
    assert req["coverage"] == "covered_via_crosswalk"
    assert req["borrowed_from"][0]["code"] == "A1"
    assert req["crosswalks"][0]["framework"] == "Framework A"

    matrix = (await auth_client.get("/api/frameworks/crosswalk-matrix")).json()
    assert matrix["total"] == 1
