"""Tests for the per-user iCal review-calendar feed."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc, calendar_service
from grcen.services.auth import create_user


@pytest.fixture
async def feed_user(pool):
    return await create_user(
        pool, f"cal_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER
    )


async def _reviewable_asset(pool, name="AUP", due="2027-01-15"):
    return await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name=name, status="active",
        metadata_={"review_date": due},
    )


# ── token lifecycle ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enable_rotate_disable_feed(pool, feed_user):
    assert await calendar_service.get_token(pool, feed_user.id) is None
    t1 = await calendar_service.enable_feed(pool, feed_user.id)
    assert await calendar_service.get_token(pool, feed_user.id) == t1
    t2 = await calendar_service.enable_feed(pool, feed_user.id)  # rotate
    assert t2 != t1
    assert await calendar_service.user_for_token(pool, t1) is None
    assert (await calendar_service.user_for_token(pool, t2))["id"] == feed_user.id
    await calendar_service.disable_feed(pool, feed_user.id)
    assert await calendar_service.user_for_token(pool, t2) is None


# ── ics rendering ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feed_contains_review_events(client, pool, feed_user):
    await _reviewable_asset(pool, name="Acceptable Use Policy", due="2027-01-15")
    token = await calendar_service.enable_feed(pool, feed_user.id)

    resp = await client.get(f"/calendar/{token}/grcen.ics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    body = resp.text
    assert "BEGIN:VCALENDAR" in body
    assert "DTSTART;VALUE=DATE:20270115" in body
    assert "Policy review due: Acceptable Use Policy" in body
    assert body.endswith("END:VCALENDAR\r\n")


@pytest.mark.asyncio
async def test_feed_escapes_special_characters(pool, feed_user):
    reviews = [{
        "id": uuid.uuid4(), "name": "Policy; with, commas", "type": "policy",
        "owner": None, "due_field": "review_date", "status": "on_track",
        "due_date": __import__("datetime").date(2027, 3, 1),
    }]
    ics = calendar_service.build_ics(reviews, base_url="http://x")
    assert "Policy\\; with\\, commas" in ics


@pytest.mark.asyncio
async def test_unknown_token_404s(client):
    resp = await client.get("/calendar/not-a-real-token/grcen.ics")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_settings_routes_toggle_feed(auth_client):
    resp = await auth_client.post("/settings/calendar/enable", follow_redirects=False)
    assert resp.status_code == 302
    page = await auth_client.get("/settings")
    assert "/calendar/" in page.text and "grcen.ics" in page.text

    resp = await auth_client.post("/settings/calendar/disable", follow_redirects=False)
    assert resp.status_code == 302
    page = await auth_client.get("/settings")
    assert "Generate feed URL" in page.text
