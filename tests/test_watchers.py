"""Tests for asset watchers: subscribe, notify on update/delete, page routes."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc, watcher_service
from grcen.services.auth import create_user


@pytest.fixture
async def watcher_user(pool):
    return await create_user(
        pool, f"watcher_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )


async def _asset(pool, name="Watched Policy"):
    return await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name=name, status="active"
    )


# ── service layer ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_unwatch_roundtrip(pool, watcher_user):
    asset = await _asset(pool)
    org = watcher_user.organization_id

    assert not await watcher_service.is_watching(pool, asset.id, watcher_user.id)
    assert await watcher_service.watch(pool, asset.id, watcher_user.id, organization_id=org)
    assert await watcher_service.is_watching(pool, asset.id, watcher_user.id)
    # Watching twice is a no-op, not an error.
    assert await watcher_service.watch(pool, asset.id, watcher_user.id, organization_id=org)

    watched = await watcher_service.watched_assets(pool, watcher_user.id, organization_id=org)
    assert [w["name"] for w in watched] == ["Watched Policy"]

    assert await watcher_service.unwatch(pool, asset.id, watcher_user.id)
    assert not await watcher_service.is_watching(pool, asset.id, watcher_user.id)


@pytest.mark.asyncio
async def test_notify_watchers_skips_actor(pool, watcher_user):
    asset = await _asset(pool)
    org = watcher_user.organization_id
    actor = await create_user(pool, f"actor_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.EDITOR)
    await watcher_service.watch(pool, asset.id, watcher_user.id, organization_id=org)
    await watcher_service.watch(pool, asset.id, actor.id, organization_id=org)

    n = await watcher_service.notify_watchers(
        pool, asset_id=asset.id, asset_name=asset.name, organization_id=org,
        action="update", actor_id=actor.id, actor_username=actor.username,
    )
    assert n == 1
    rows = await pool.fetch(
        "SELECT user_id, title FROM notifications WHERE user_id IS NOT NULL"
    )
    assert len(rows) == 1
    assert rows[0]["user_id"] == watcher_user.id
    assert "Watched Policy" in rows[0]["title"]


# ── wiring: REST update / delete notify watchers ─────────────────────────


@pytest.mark.asyncio
async def test_rest_update_notifies_watcher(auth_client, pool, watcher_user):
    asset = await _asset(pool)
    await watcher_service.watch(
        pool, asset.id, watcher_user.id,
        organization_id=watcher_user.organization_id,
    )
    resp = await auth_client.put(
        f"/api/assets/{asset.id}", json={"description": "now different"}
    )
    assert resp.status_code == 200
    row = await pool.fetchrow(
        "SELECT title, message FROM notifications WHERE user_id = $1", watcher_user.id
    )
    assert row is not None
    assert "updated" in row["title"]


@pytest.mark.asyncio
async def test_rest_delete_notifies_watcher(auth_client, pool, watcher_user):
    asset = await _asset(pool, name="Doomed System")
    await watcher_service.watch(
        pool, asset.id, watcher_user.id,
        organization_id=watcher_user.organization_id,
    )
    resp = await auth_client.delete(f"/api/assets/{asset.id}")
    assert resp.status_code == 204
    row = await pool.fetchrow(
        "SELECT title FROM notifications WHERE user_id = $1", watcher_user.id
    )
    assert row is not None
    assert "deleted" in row["title"]
    assert "Doomed System" in row["title"]


# ── page routes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_button_routes(auth_client, pool):
    asset = await _asset(pool)
    resp = await auth_client.post(f"/assets/{asset.id}/watch", follow_redirects=False)
    assert resp.status_code == 302

    detail = await auth_client.get(f"/assets/{asset.id}")
    assert "Unwatch" in detail.text

    resp = await auth_client.post(f"/assets/{asset.id}/unwatch", follow_redirects=False)
    assert resp.status_code == 302
    detail = await auth_client.get(f"/assets/{asset.id}")
    assert ">Watch" in detail.text.replace("\n", "").replace("  ", "")


@pytest.mark.asyncio
async def test_watch_unknown_asset_404s(auth_client):
    resp = await auth_client.post(
        f"/assets/{uuid.uuid4()}/watch", follow_redirects=False
    )
    assert resp.status_code == 404
