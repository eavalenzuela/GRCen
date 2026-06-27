"""Tests for the external controls-catalog sync (autocomply → GRCen)."""
import pytest

from grcen.services import catalog_sync, framework_service, organization_service


def _catalog():
    return {
        "catalog_version": "1",
        "source": "autocomply",
        "frameworks": [
            {
                "ref": "soc2",
                "name": "SOC 2",
                "description": "Trust Services Criteria",
                "metadata": {"version": "2017 (rev. 2022)", "governing_body": "AICPA"},
                "requirements": [
                    {"ref": "soc2:CC6.1", "name": "CC6.1 — Logical access",
                     "reference_id": "CC6.1", "category": "Common Criteria"},
                    {"ref": "soc2:CC7.2", "name": "CC7.2 — Monitoring",
                     "reference_id": "CC7.2"},
                ],
            },
        ],
        "controls": [
            {"ref": "01.a", "name": "Access Control Policy",
             "metadata": {"control_type": "preventive"},
             "satisfies": ["soc2:CC6.1"]},
            {"ref": "07.b", "name": "Security Monitoring",
             "satisfies": ["soc2:CC7.2"]},
        ],
    }


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


@pytest.mark.asyncio
async def test_sync_creates_assets_and_edges(pool):
    org = await _org(pool)
    result = await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    assert not result.errors
    assert result.frameworks == 1
    assert result.requirements == 2
    assert result.controls == 2
    # 1 framework + 2 requirements + 2 controls
    assert result.assets_created == 5
    assert result.assets_updated == 0
    # 2 parent_of + 2 satisfies
    assert result.edges_created == 4

    assets = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'autocomply' AND organization_id = $1",
        org,
    )
    assert assets == 5
    # requirement metadata got the derived framework name + reference_id
    meta = await pool.fetchval(
        "SELECT metadata FROM assets WHERE source_ref = 'requirement:soc2:CC6.1'"
    )
    import json
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["framework"] == "SOC 2"
    assert meta["reference_id"] == "CC6.1"


@pytest.mark.asyncio
async def test_resync_is_idempotent(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)
    result = await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    # Nothing new on the second run.
    assert result.assets_created == 0
    assert result.assets_updated == 5
    assert result.edges_created == 0
    assert result.edges_updated == 4
    assert result.edges_pruned == 0

    total = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'autocomply' AND organization_id = $1",
        org,
    )
    assert total == 5  # no duplicates


@pytest.mark.asyncio
async def test_resync_updates_in_place(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    cat = _catalog()
    cat["frameworks"][0]["name"] = "SOC 2 Type II"
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)

    assert result.assets_created == 0
    name = await pool.fetchval(
        "SELECT name FROM assets WHERE source_ref = 'framework:soc2'"
    )
    assert name == "SOC 2 Type II"


@pytest.mark.asyncio
async def test_removed_mapping_is_pruned(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    cat = _catalog()
    cat["controls"][0]["satisfies"] = []  # 01.a no longer satisfies CC6.1
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)

    assert result.edges_pruned == 1
    edges = await pool.fetchval(
        """SELECT count(*) FROM relationships
           WHERE source = 'autocomply' AND relationship_type = 'satisfies'
             AND organization_id = $1""",
        org,
    )
    assert edges == 1  # only CC7.2's satisfies edge remains


@pytest.mark.asyncio
async def test_stale_asset_reported_then_pruned(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    cat = _catalog()
    cat["controls"].pop()  # drop 07.b entirely

    # Default run reports it but keeps it.
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    assert "control:07.b" in result.stale_assets
    assert result.assets_pruned == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source_ref = 'control:07.b'"
    ) == 1

    # With --prune it's deleted.
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org, prune=True)
    assert result.assets_pruned == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source_ref = 'control:07.b'"
    ) == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    result = await catalog_sync.sync_catalog(
        pool, _catalog(), organization_id=org, dry_run=True
    )
    assert result.dry_run
    assert result.assets_created == 5  # counts reflect what *would* happen
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'autocomply'"
    ) == 0


@pytest.mark.asyncio
async def test_invalid_catalog_rejected(pool):
    org = await _org(pool)
    bad = {"frameworks": [{"name": "no ref"}]}
    result = await catalog_sync.sync_catalog(pool, bad, organization_id=org)
    assert result.errors
    assert result.assets_created == 0


@pytest.mark.asyncio
async def test_dangling_satisfies_rejected(pool):
    org = await _org(pool)
    cat = _catalog()
    cat["controls"][0]["satisfies"] = ["soc2:NOPE"]
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    assert any("unknown requirement" in e for e in result.errors)


@pytest.mark.asyncio
async def test_dashboard_coverage_lights_up(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    soc2 = next(s for s in summaries if s.name == "SOC 2")
    assert soc2.requirement_count == 2
    # Both requirements are satisfied by a control's `satisfies` edge.
    assert soc2.satisfied_count == 2
    assert soc2.coverage_percent == 100


def _catalog_xwalk():
    """Two frameworks with an equivalent requirement crosswalked between them."""
    return {
        "catalog_version": "1",
        "source": "autocomply",
        "frameworks": [
            {
                "ref": "soc2", "name": "SOC 2",
                "requirements": [
                    {"ref": "soc2:CC6.1", "name": "CC6.1 — Logical access"},
                ],
            },
            {
                "ref": "iso27001", "name": "ISO 27001",
                "requirements": [
                    {"ref": "iso27001:A.5.15", "name": "A.5.15 — Access control"},
                ],
            },
        ],
        "controls": [],
        "crosswalks": [
            {"from": "iso27001:A.5.15", "to": "soc2:CC6.1",
             "relationship": "equivalent", "confidence": "high"},
        ],
    }


@pytest.mark.asyncio
async def test_crosswalk_edge_created(pool):
    org = await _org(pool)
    result = await catalog_sync.sync_catalog(pool, _catalog_xwalk(), organization_id=org)
    assert not result.errors
    assert result.crosswalks == 1
    row = await pool.fetchrow(
        """SELECT relationship_type, description FROM relationships
           WHERE source = 'autocomply' AND relationship_type = 'cross_maps'
             AND organization_id = $1""",
        org,
    )
    assert row is not None
    assert row["relationship_type"] == "cross_maps"
    assert "equivalent" in row["description"]
    assert "confidence: high" in row["description"]


@pytest.mark.asyncio
async def test_crosswalk_is_idempotent_and_pruned(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog_xwalk(), organization_id=org)
    again = await catalog_sync.sync_catalog(pool, _catalog_xwalk(), organization_id=org)
    assert again.crosswalks == 1
    assert again.edges_created == 0  # no new edges on resync

    dropped = _catalog_xwalk()
    dropped["crosswalks"] = []
    pruned = await catalog_sync.sync_catalog(pool, dropped, organization_id=org)
    assert pruned.crosswalks == 0
    assert pruned.edges_pruned == 1
    remaining = await pool.fetchval(
        """SELECT count(*) FROM relationships
           WHERE relationship_type = 'cross_maps' AND organization_id = $1""",
        org,
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_crosswalk_unknown_ref_rejected(pool):
    org = await _org(pool)
    cat = _catalog_xwalk()
    cat["crosswalks"][0]["to"] = "soc2:NOPE"
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    assert any("unknown requirement 'soc2:NOPE'" in e for e in result.errors)
    assert result.assets_created == 0


@pytest.mark.asyncio
async def test_crosswalk_self_and_duplicate_rejected(pool):
    org = await _org(pool)
    cat = _catalog_xwalk()
    cat["crosswalks"] = [
        {"from": "soc2:CC6.1", "to": "soc2:CC6.1"},  # self
        {"from": "iso27001:A.5.15", "to": "soc2:CC6.1"},
        {"from": "soc2:CC6.1", "to": "iso27001:A.5.15"},  # reverse dup
    ]
    result = await catalog_sync.sync_catalog(pool, cat, organization_id=org)
    assert any("to itself" in e for e in result.errors)
    assert any("duplicates the mapping" in e for e in result.errors)


@pytest.mark.asyncio
async def test_human_edges_survive_resync(pool):
    """A relationship a human adds to a synced control isn't pruned."""
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    ctrl_id = await pool.fetchval(
        "SELECT id FROM assets WHERE source_ref = 'control:01.a'"
    )
    sys_id = await pool.fetchval(
        """INSERT INTO assets (type, name, organization_id)
           VALUES ('system', 'Prod Cluster', $1) RETURNING id""",
        org,
    )
    # Human-authored edge: source IS NULL.
    await pool.execute(
        """INSERT INTO relationships (source_asset_id, target_asset_id,
                                      relationship_type, organization_id)
           VALUES ($1, $2, 'protects', $3)""",
        ctrl_id, sys_id, org,
    )

    await catalog_sync.sync_catalog(pool, _catalog(), organization_id=org)

    survived = await pool.fetchval(
        """SELECT count(*) FROM relationships
           WHERE relationship_type = 'protects' AND source IS NULL""",
    )
    assert survived == 1
