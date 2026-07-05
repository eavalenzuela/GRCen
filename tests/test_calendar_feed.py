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


# ── E4 enrichments: audit dates, VALARM, per-user filter ─────────────────


@pytest.mark.asyncio
async def test_audit_end_date_appears_as_event(pool):
    from grcen.services import organization_service, review_service
    org = await organization_service.get_default_org_id(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.AUDIT, name="SOC 2 Type II",
        metadata_={"end_date": "2027-06-30"}, organization_id=org)
    reviews = await review_service.get_reviews(pool, organization_id=org)
    audit = next((r for r in reviews if r["type"] == "audit"), None)
    assert audit is not None and audit["name"] == "SOC 2 Type II"
    ics = calendar_service.build_ics(reviews, base_url="http://x")
    assert "Audit: SOC 2 Type II" in ics


def test_finding_summary_phrasing():
    from datetime import date
    reviews = [{
        "id": uuid.uuid4(), "name": "SQLi", "type": "finding", "owner": None,
        "due_field": "due_date", "status": "overdue", "due_date": date(2027, 1, 1),
    }]
    ics = calendar_service.build_ics(reviews, base_url="http://x")
    assert "Finding remediation due: SQLi" in ics


def test_build_ics_valarm_opt_in():
    from datetime import date
    reviews = [{
        "id": uuid.uuid4(), "name": "X", "type": "policy", "owner": None,
        "due_field": "review_date", "status": "on_track", "due_date": date(2027, 1, 1),
    }]
    assert "BEGIN:VALARM" not in calendar_service.build_ics(reviews, base_url="http://x")
    with_alarm = calendar_service.build_ics(reviews, base_url="http://x", alarm_days=3)
    assert "BEGIN:VALARM" in with_alarm
    assert "TRIGGER:-P3D" in with_alarm


@pytest.mark.asyncio
async def test_feed_valarm_default_and_disable(client, pool, feed_user):
    await _reviewable_asset(pool, name="AUP", due="2027-01-15")
    token = await calendar_service.enable_feed(pool, feed_user.id)

    body = (await client.get(f"/calendar/{token}/grcen.ics")).text
    assert "BEGIN:VALARM" in body            # reminders on by default
    assert "TRIGGER:-P7D" in body

    body0 = (await client.get(f"/calendar/{token}/grcen.ics?alarm=0")).text
    assert "BEGIN:VALARM" not in body0

    body14 = (await client.get(f"/calendar/{token}/grcen.ics?alarm=14")).text
    assert "TRIGGER:-P14D" in body14


@pytest.mark.asyncio
async def test_feed_scope_mine_filters_to_owned(client, pool, feed_user):
    from grcen.services.auth import set_person_asset_link
    org = feed_user.organization_id
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Me", organization_id=org)
    await set_person_asset_link(pool, feed_user.id, person.id)
    await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="My Policy",
        metadata_={"review_date": "2027-02-01"}, owner_id=person.id, organization_id=org)
    await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="Someone Elses",
        metadata_={"review_date": "2027-02-02"}, organization_id=org)
    token = await calendar_service.enable_feed(pool, feed_user.id)

    mine = (await client.get(f"/calendar/{token}/grcen.ics?scope=mine")).text
    assert "My Policy" in mine
    assert "Someone Elses" not in mine
    assert "My Assets" in mine  # calendar name reflects the filter

    both = (await client.get(f"/calendar/{token}/grcen.ics")).text
    assert "My Policy" in both and "Someone Elses" in both


@pytest.mark.asyncio
async def test_feed_scope_mine_empty_when_no_person_link(client, pool, feed_user):
    # feed_user has no linked Person → owns nothing → empty (but valid) calendar
    await _reviewable_asset(pool, name="Org Policy", due="2027-01-15")
    token = await calendar_service.enable_feed(pool, feed_user.id)
    body = (await client.get(f"/calendar/{token}/grcen.ics?scope=mine")).text
    assert "BEGIN:VCALENDAR" in body
    assert "Org Policy" not in body


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
