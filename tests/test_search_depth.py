"""Tests for deeper search: relationship descriptions, metadata values (S3),
and description-aware /api/assets/search (S4)."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import relationship as rel_svc
from grcen.services.auth import create_user


async def _admin(pool):
    return await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)


@pytest.mark.asyncio
async def test_search_matches_relationship_description(pool):
    admin = await _admin(pool)
    a = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Alpha", updated_by=admin.id)
    b = await asset_svc.create_asset(pool, type=AssetType.SYSTEM, name="Beta", updated_by=admin.id)
    await rel_svc.create_relationship(
        pool, source_asset_id=a.id, target_asset_id=b.id,
        relationship_type="depends_on", description="handles PCI cardholder data",
    )
    items, _ = await asset_svc.list_assets(pool, q="cardholder")
    names = {x.name for x in items}
    # both endpoints of the matching relationship surface
    assert "Alpha" in names
    assert "Beta" in names


@pytest.mark.asyncio
async def test_search_matches_metadata_value_but_not_sensitive(pool):
    admin = await _admin(pool)
    # On Person, 'department' is non-sensitive but 'email' is sensitive.
    await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Carol",
        metadata_={"department": "Finance", "email": "secret@example.com"},
        updated_by=admin.id,
    )
    by_dept, _ = await asset_svc.list_assets(pool, q="Finance")
    assert any(x.name == "Carol" for x in by_dept)

    # The sensitive value must NOT be searchable (no PII side channel).
    by_email, _ = await asset_svc.list_assets(pool, q="secret@example.com")
    assert not any(x.name == "Carol" for x in by_email)


@pytest.mark.asyncio
async def test_search_assets_matches_description(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Zeta",
        description="the payments gateway", updated_by=admin.id,
    )
    res = await asset_svc.search_assets(pool, "payments gateway")
    assert any(x.name == "Zeta" for x in res)


@pytest.mark.asyncio
async def test_api_search_matches_description(auth_client):
    await auth_client.post(
        "/api/assets/",
        json={"type": "system", "name": "Onyx", "description": "core billing engine"},
    )
    res = await auth_client.get("/api/assets/search?q=billing engine")
    assert res.status_code == 200
    assert any(x["name"] == "Onyx" for x in res.json())


@pytest.mark.asyncio
async def test_search_assets_fuzzy_typo(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Kubernetes Cluster", updated_by=admin.id
    )
    # "kubernets" is NOT a substring of "Kubernetes Cluster" — only fuzzy
    # (pg_trgm word similarity) can match it.
    res = await asset_svc.search_assets(pool, "kubernets")
    assert any(a.name == "Kubernetes Cluster" for a in res)


@pytest.mark.asyncio
async def test_search_assets_ranks_exact_substring_first(pool):
    admin = await _admin(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Payment Gateway", updated_by=admin.id
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Payment Gatewy", updated_by=admin.id
    )
    res = await asset_svc.search_assets(pool, "Payment Gateway")
    names = [a.name for a in res]
    assert names[0] == "Payment Gateway"   # exact-substring match ranks first
    assert "Payment Gatewy" in names       # fuzzy match still returned


@pytest.mark.asyncio
async def test_api_search_fuzzy(auth_client):
    # The relationship target picker calls /api/assets/search — it should be
    # typo-tolerant, not just exact-substring.
    await auth_client.post("/api/assets/", json={"type": "system", "name": "Kubernetes Cluster"})
    res = await auth_client.get("/api/assets/search?q=kubernets")
    assert res.status_code == 200
    assert any(x["name"] == "Kubernetes Cluster" for x in res.json())
