"""Tests for the generic external-source upsert primitive (roadmap I0)."""
import pytest

from grcen.models.asset import AssetType
from grcen.services import external_sync, organization_service


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates_in_place(pool):
    org = await _org(pool)
    r1 = await external_sync.upsert_external_asset(
        pool, organization_id=org, source="hris:test", source_ref="emp-1",
        asset_type=AssetType.PERSON.value, name="Ada", metadata={"department": "Eng"},
    )
    assert r1.inserted is True
    r2 = await external_sync.upsert_external_asset(
        pool, organization_id=org, source="hris:test", source_ref="emp-1",
        asset_type=AssetType.PERSON.value, name="Ada Lovelace",
        metadata={"department": "R&D"},
    )
    assert r2.inserted is False
    assert r2.id == r1.id
    count = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'hris:test' AND organization_id = $1",
        org,
    )
    assert count == 1
    assert await pool.fetchval("SELECT name FROM assets WHERE id = $1", r1.id) == "Ada Lovelace"


@pytest.mark.asyncio
async def test_upsert_updates_status(pool):
    """Unlike catalog_sync, the authoritative-source upsert owns status too."""
    org = await _org(pool)
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="scim", source_ref="u1",
        asset_type=AssetType.PERSON.value, name="Bob", status="active",
    )
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="scim", source_ref="u1",
        asset_type=AssetType.PERSON.value, name="Bob", status="archived",
    )
    status = await pool.fetchval(
        "SELECT status FROM assets WHERE source = 'scim' AND source_ref = 'u1'"
    )
    assert status == "archived"


@pytest.mark.asyncio
async def test_distinct_source_refs_are_distinct_rows(pool):
    org = await _org(pool)
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="cmdb", source_ref="a",
        asset_type=AssetType.SYSTEM.value, name="A",
    )
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="cmdb", source_ref="b",
        asset_type=AssetType.SYSTEM.value, name="B",
    )
    count = await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'cmdb' AND organization_id = $1", org
    )
    assert count == 2


@pytest.mark.asyncio
async def test_empty_source_or_ref_rejected(pool):
    org = await _org(pool)
    with pytest.raises(ValueError):
        await external_sync.upsert_external_asset(
            pool, organization_id=org, source="", source_ref="x",
            asset_type=AssetType.PERSON.value, name="X",
        )
    with pytest.raises(ValueError):
        await external_sync.upsert_external_asset(
            pool, organization_id=org, source="hris", source_ref="",
            asset_type=AssetType.PERSON.value, name="X",
        )


@pytest.mark.asyncio
async def test_prune_stale_assets_scoped_to_source(pool):
    org = await _org(pool)
    # human-authored person (source IS NULL) — must survive
    await pool.execute(
        "INSERT INTO assets (type, name, organization_id) VALUES ('person', 'Human', $1)",
        org,
    )
    # a different source's person — must survive
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="other", source_ref="o1",
        asset_type=AssetType.PERSON.value, name="Other",
    )
    # our source: two rows, one goes stale
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="hris", source_ref="keep",
        asset_type=AssetType.PERSON.value, name="Keep",
    )
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="hris", source_ref="gone",
        asset_type=AssetType.PERSON.value, name="Gone",
    )

    stale = await external_sync.prune_stale_assets(
        pool, organization_id=org, source="hris", seen_refs=["keep"], delete=True
    )
    assert stale == ["gone"]
    # human + other + hris:keep survive; only hris:gone deleted
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE organization_id = $1", org
    ) == 3
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'hris'") == 1


@pytest.mark.asyncio
async def test_prune_reports_without_delete(pool):
    org = await _org(pool)
    await external_sync.upsert_external_asset(
        pool, organization_id=org, source="hris", source_ref="x",
        asset_type=AssetType.PERSON.value, name="X",
    )
    stale = await external_sync.prune_stale_assets(
        pool, organization_id=org, source="hris", seen_refs=[], delete=False
    )
    assert stale == ["x"]
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'hris'") == 1


@pytest.mark.asyncio
async def test_upsert_edge_and_prune(pool):
    org = await _org(pool)
    a = await external_sync.upsert_external_asset(
        pool, organization_id=org, source="s", source_ref="a",
        asset_type=AssetType.SYSTEM.value, name="A",
    )
    b = await external_sync.upsert_external_asset(
        pool, organization_id=org, source="s", source_ref="b",
        asset_type=AssetType.SYSTEM.value, name="B",
    )
    e = await external_sync.upsert_external_edge(
        pool, organization_id=org, source="s", source_ref="edge:a:b",
        source_asset_id=a.id, target_asset_id=b.id, relationship_type="depends_on",
    )
    assert e.inserted is True
    # re-upsert is idempotent
    e2 = await external_sync.upsert_external_edge(
        pool, organization_id=org, source="s", source_ref="edge:a:b",
        source_asset_id=a.id, target_asset_id=b.id, relationship_type="depends_on",
    )
    assert e2.inserted is False
    assert e2.id == e.id
    pruned = await external_sync.prune_stale_edges(
        pool, organization_id=org, source="s", seen_refs=[]
    )
    assert pruned == 1
