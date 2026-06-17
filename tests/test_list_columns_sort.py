"""Tests for type-aware list columns (O1) and custom-field / updated_at sort (O2)."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user


async def _admin(pool):
    return await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)


@pytest.mark.asyncio
async def test_sort_by_updated_at(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="S-upd-1", updated_by=admin.id)
    await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="S-upd-2", updated_by=admin.id)
    items, _ = await asset_svc.list_assets(pool, sort="updated_at", order="desc")
    assert len(items) >= 2  # updated_at is now a valid sort, no error


@pytest.mark.asyncio
async def test_sort_by_custom_field(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(pool, type=AssetType.RISK, name="R-low",
                                 metadata_={"severity": "a-low"}, updated_by=admin.id)
    await asset_svc.create_asset(pool, type=AssetType.RISK, name="R-high",
                                 metadata_={"severity": "z-high"}, updated_by=admin.id)
    await asset_svc.create_asset(pool, type=AssetType.RISK, name="R-none", updated_by=admin.id)

    items, _ = await asset_svc.list_assets(
        pool, asset_type=AssetType.RISK, sort="meta.severity", order="asc"
    )
    names = [a.name for a in items]
    assert names.index("R-low") < names.index("R-high")
    assert names[-1] == "R-none"  # NULLS LAST


@pytest.mark.asyncio
async def test_meta_sort_key_injection_safe(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Safe", updated_by=admin.id)
    # A non-identifier sort key is rejected by the validator and falls back to
    # the default ordering — no SQL error, no injection.
    items, _ = await asset_svc.list_assets(pool, sort="meta.evil'; DROP TABLE assets;--")
    assert any(a.name == "Safe" for a in items)


@pytest.mark.asyncio
async def test_assets_page_shows_type_columns(auth_client):
    await auth_client.post("/api/assets/", json={"type": "risk", "name": "Col Risk"})
    page = await auth_client.get("/assets?type=risk")
    assert page.status_code == 200
    # The selected type's non-sensitive custom fields become sortable columns.
    assert "Severity" in page.text
    assert "Likelihood" in page.text
    assert "sort=meta.severity" in page.text
    # No type filter → no custom-field columns.
    plain = await auth_client.get("/assets")
    assert "sort=meta.severity" not in plain.text
