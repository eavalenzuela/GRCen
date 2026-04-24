"""Tests for the cross-cutting tag vocabulary."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import tag_service
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _mk(pool, admin_id, name, tags):
    return await asset_svc.create_asset(
        pool,
        type=AssetType.SYSTEM,
        name=name,
        status="active",
        updated_by=admin_id,
        tags=tags,
    )


# ── aggregation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_with_counts(pool, admin_user):
    await _mk(pool, admin_user.id, "A", ["soc2", "gdpr"])
    await _mk(pool, admin_user.id, "B", ["soc2"])
    await _mk(pool, admin_user.id, "C", [])
    tags = await tag_service.list_tags_with_counts(pool)
    names = {t.name: t.asset_count for t in tags}
    assert names == {"soc2": 2, "gdpr": 1}


@pytest.mark.asyncio
async def test_list_tags_empty_when_no_tags(pool, admin_user):
    await _mk(pool, admin_user.id, "A", [])
    assert await tag_service.list_tags_with_counts(pool) == []


# ── rename ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rename_tag_across_multiple_assets(pool, admin_user):
    a1 = await _mk(pool, admin_user.id, "A1", ["old-name"])
    a2 = await _mk(pool, admin_user.id, "A2", ["old-name", "other"])

    n = await tag_service.rename_tag(pool, "old-name", "new-name")
    assert n == 2

    fresh_a1 = await asset_svc.get_asset(pool, a1.id)
    fresh_a2 = await asset_svc.get_asset(pool, a2.id)
    assert "new-name" in fresh_a1.tags
    assert "old-name" not in fresh_a1.tags
    assert "new-name" in fresh_a2.tags
    assert "other" in fresh_a2.tags


@pytest.mark.asyncio
async def test_rename_dedupes_when_target_already_present(pool, admin_user):
    a = await _mk(pool, admin_user.id, "A", ["alpha", "beta"])
    await tag_service.rename_tag(pool, "alpha", "beta")
    fresh = await asset_svc.get_asset(pool, a.id)
    # Only one copy of beta survives; alpha is gone.
    assert sorted(fresh.tags) == ["beta"]


@pytest.mark.asyncio
async def test_rename_same_name_is_noop(pool, admin_user):
    a = await _mk(pool, admin_user.id, "A", ["x"])
    n = await tag_service.rename_tag(pool, "x", "x")
    assert n == 0
    fresh = await asset_svc.get_asset(pool, a.id)
    assert fresh.tags == ["x"]


@pytest.mark.asyncio
async def test_rename_nonexistent_tag_is_noop(pool, admin_user):
    a = await _mk(pool, admin_user.id, "A", ["x"])
    n = await tag_service.rename_tag(pool, "missing", "y")
    assert n == 0
    fresh = await asset_svc.get_asset(pool, a.id)
    assert fresh.tags == ["x"]


# ── delete ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_tag_removes_from_all_assets(pool, admin_user):
    a1 = await _mk(pool, admin_user.id, "A1", ["keep", "drop"])
    a2 = await _mk(pool, admin_user.id, "A2", ["drop"])

    n = await tag_service.delete_tag(pool, "drop")
    assert n == 2

    fresh_a1 = await asset_svc.get_asset(pool, a1.id)
    fresh_a2 = await asset_svc.get_asset(pool, a2.id)
    assert "drop" not in (fresh_a1.tags or [])
    assert "keep" in fresh_a1.tags
    assert (fresh_a2.tags or []) == []


# ── asset list filter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asset_list_filters_by_tag(pool, admin_user):
    await _mk(pool, admin_user.id, "A1", ["soc2"])
    await _mk(pool, admin_user.id, "A2", ["gdpr"])
    await _mk(pool, admin_user.id, "A3", [])

    items, total = await asset_svc.list_assets(pool, tag="soc2")
    assert total == 1
    assert items[0].name == "A1"


@pytest.mark.asyncio
async def test_asset_api_list_filters_by_tag(auth_client, pool, admin_user):
    await _mk(pool, admin_user.id, "A1", ["soc2"])
    await _mk(pool, admin_user.id, "A2", ["gdpr"])
    resp = await auth_client.get("/api/assets/?tag=soc2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "A1"


# ── tags page and RBAC ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_page_lists_tags(auth_client, pool, admin_user):
    await _mk(pool, admin_user.id, "A", ["alpha", "beta"])
    resp = await auth_client.get("/tags")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "beta" in resp.text


@pytest.mark.asyncio
async def test_tags_rename_flow(auth_client, pool, admin_user):
    from tests.conftest import _extract_csrf_from_session_cookie

    a = await _mk(pool, admin_user.id, "A", ["old"])
    csrf = _extract_csrf_from_session_cookie(auth_client)
    resp = await auth_client.post(
        "/tags/old/rename",
        data={"new_name": "new", "csrf_token": csrf},
    )
    assert resp.status_code in (302, 303)
    fresh = await asset_svc.get_asset(pool, a.id)
    assert fresh.tags == ["new"]


@pytest.mark.asyncio
async def test_tags_delete_flow(auth_client, pool, admin_user):
    from tests.conftest import _extract_csrf_from_session_cookie

    a = await _mk(pool, admin_user.id, "A", ["doomed", "kept"])
    csrf = _extract_csrf_from_session_cookie(auth_client)
    resp = await auth_client.post(
        "/tags/doomed/delete",
        data={"csrf_token": csrf},
    )
    assert resp.status_code in (302, 303)
    fresh = await asset_svc.get_asset(pool, a.id)
    assert fresh.tags == ["kept"]


@pytest.mark.asyncio
async def test_api_tags_endpoint(auth_client, pool, admin_user):
    await _mk(pool, admin_user.id, "A", ["x"])
    await _mk(pool, admin_user.id, "B", ["x", "y"])
    resp = await auth_client.get("/api/tags/")
    assert resp.status_code == 200
    body = resp.json()
    counts = {row["name"]: row["asset_count"] for row in body}
    assert counts == {"x": 2, "y": 1}
