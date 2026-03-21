"""Tests for advanced asset search and filtering."""

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc


@pytest.fixture
async def sample_assets(pool):
    """Create a variety of assets for search testing."""
    a1 = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Alice Smith", status="active",
        owner="HR", description="Senior engineer",
    )
    a2 = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Prod API", status="active",
        owner="Platform Team", metadata_={"environment": "production", "criticality": "critical"},
    )
    a3 = await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="Data Retention Policy", status="draft",
        owner="Legal",
    )
    a4 = await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="Vendor Lock-in", status="active",
        owner="Alice Smith", metadata_={"likelihood": "likely", "impact": "major"},
    )
    a5 = await asset_svc.create_asset(
        pool, type=AssetType.DEVICE, name="Office Laptop Fleet", status="inactive",
        owner="IT",
    )
    return [a1, a2, a3, a4, a5]


# --- Service-level tests ---


@pytest.mark.asyncio
async def test_search_by_name(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, q="Alice")
    # Matches "Alice Smith" by name AND "Vendor Lock-in" by owner
    assert total == 2
    names = {a.name for a in items}
    assert "Alice Smith" in names


@pytest.mark.asyncio
async def test_search_by_description(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, q="engineer")
    assert total == 1
    assert items[0].name == "Alice Smith"


@pytest.mark.asyncio
async def test_search_by_owner(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, q="Platform")
    assert total == 1
    assert items[0].name == "Prod API"


@pytest.mark.asyncio
async def test_filter_by_status(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, status="draft")
    assert total == 1
    assert items[0].name == "Data Retention Policy"


@pytest.mark.asyncio
async def test_filter_by_owner(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, owner="Legal")
    assert total == 1
    assert items[0].name == "Data Retention Policy"


@pytest.mark.asyncio
async def test_filter_by_owner_partial(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool, owner="alice")
    assert total == 1
    assert items[0].name == "Vendor Lock-in"


@pytest.mark.asyncio
async def test_filter_by_type_and_status(pool, sample_assets):
    items, total = await asset_svc.list_assets(
        pool, asset_type=AssetType.DEVICE, status="inactive"
    )
    assert total == 1
    assert items[0].name == "Office Laptop Fleet"


@pytest.mark.asyncio
async def test_filter_by_metadata(pool, sample_assets):
    items, total = await asset_svc.list_assets(
        pool, metadata_filters={"environment": "production"}
    )
    assert total == 1
    assert items[0].name == "Prod API"


@pytest.mark.asyncio
async def test_filter_by_metadata_no_match(pool, sample_assets):
    items, total = await asset_svc.list_assets(
        pool, metadata_filters={"environment": "staging"}
    )
    assert total == 0


@pytest.mark.asyncio
async def test_combined_filters(pool, sample_assets):
    items, total = await asset_svc.list_assets(
        pool, q="Prod", status="active", metadata_filters={"criticality": "critical"}
    )
    assert total == 1
    assert items[0].name == "Prod API"


@pytest.mark.asyncio
async def test_created_after_filter(pool, sample_assets):
    # All assets were just created, so filtering with a future date should return 0
    items, total = await asset_svc.list_assets(pool, created_after="2099-01-01")
    assert total == 0


@pytest.mark.asyncio
async def test_created_before_filter(pool, sample_assets):
    # All assets were just created, so filtering with a past date should return 0
    items, total = await asset_svc.list_assets(pool, created_before="2020-01-01")
    assert total == 0


@pytest.mark.asyncio
async def test_no_filters_returns_all(pool, sample_assets):
    items, total = await asset_svc.list_assets(pool)
    assert total == 5


@pytest.mark.asyncio
async def test_multi_type_filter(pool, sample_assets):
    items, total = await asset_svc.list_assets(
        pool, asset_types=[AssetType.PERSON, AssetType.RISK]
    )
    assert total == 2
    names = {a.name for a in items}
    assert names == {"Alice Smith", "Vendor Lock-in"}


# --- Page-level tests ---


@pytest.mark.asyncio
async def test_asset_list_page_with_search(auth_client, pool, sample_assets):
    resp = await auth_client.get("/assets?q=Alice")
    assert resp.status_code == 200
    assert b"Alice Smith" in resp.content
    assert b"Prod API" not in resp.content


@pytest.mark.asyncio
async def test_asset_list_page_with_status_filter(auth_client, pool, sample_assets):
    resp = await auth_client.get("/assets?status=draft")
    assert resp.status_code == 200
    assert b"Data Retention Policy" in resp.content
    assert b"Prod API" not in resp.content


@pytest.mark.asyncio
async def test_asset_list_page_with_metadata_filter(auth_client, pool, sample_assets):
    resp = await auth_client.get("/assets?meta_key=environment&meta_value=production")
    assert resp.status_code == 200
    assert b"Prod API" in resp.content
    assert b"Alice Smith" not in resp.content


@pytest.mark.asyncio
async def test_asset_list_page_shows_result_count(auth_client, pool, sample_assets):
    resp = await auth_client.get("/assets?status=active")
    assert resp.status_code == 200
    assert b"3 assets found" in resp.content


@pytest.mark.asyncio
async def test_asset_list_page_no_results(auth_client, pool, sample_assets):
    resp = await auth_client.get("/assets?q=nonexistent")
    assert resp.status_code == 200
    assert b"No assets found" in resp.content
