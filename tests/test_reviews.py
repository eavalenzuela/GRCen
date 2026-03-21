"""Tests for review workflow tracking."""

from datetime import date, timedelta

import pytest

from grcen.services.review_service import review_status


# --- Unit tests ---


def test_review_status_overdue():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert review_status(yesterday) == "overdue"


def test_review_status_due_soon():
    in_15_days = (date.today() + timedelta(days=15)).isoformat()
    assert review_status(in_15_days) == "due_soon"


def test_review_status_on_track():
    in_60_days = (date.today() + timedelta(days=60)).isoformat()
    assert review_status(in_60_days) == "on_track"


def test_review_status_no_date():
    assert review_status(None) == "no_date"
    assert review_status("") == "no_date"


def test_review_status_today_is_due_soon():
    assert review_status(date.today().isoformat()) == "due_soon"


def test_review_status_boundary_30_days():
    day_30 = (date.today() + timedelta(days=30)).isoformat()
    assert review_status(day_30) == "due_soon"
    day_31 = (date.today() + timedelta(days=31)).isoformat()
    assert review_status(day_31) == "on_track"


# --- Integration tests ---


@pytest.mark.asyncio
async def test_reviews_page_loads(auth_client):
    resp = await auth_client.get("/reviews")
    assert resp.status_code == 200
    assert b"Review Tracker" in resp.content


@pytest.mark.asyncio
async def test_overdue_review_appears(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await asset_svc.create_asset(
        pool,
        type=AssetType.SYSTEM,
        name="Overdue System",
        status="active",
        metadata_={"next_review_due": yesterday},
    )

    resp = await auth_client.get("/reviews")
    assert resp.status_code == 200
    assert b"Overdue System" in resp.content
    assert b"Overdue" in resp.content


@pytest.mark.asyncio
async def test_due_soon_review_appears(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    in_10_days = (date.today() + timedelta(days=10)).isoformat()
    await asset_svc.create_asset(
        pool,
        type=AssetType.DEVICE,
        name="Due Soon Device",
        status="active",
        metadata_={"next_review_due": in_10_days},
    )

    resp = await auth_client.get("/reviews")
    assert b"Due Soon Device" in resp.content
    assert b"Due Soon" in resp.content


@pytest.mark.asyncio
async def test_on_track_review_appears(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    in_90_days = (date.today() + timedelta(days=90)).isoformat()
    await asset_svc.create_asset(
        pool,
        type=AssetType.POLICY,
        name="On Track Policy",
        status="active",
        metadata_={"review_date": in_90_days},
    )

    resp = await auth_client.get("/reviews")
    assert b"On Track Policy" in resp.content
    assert b"On Track" in resp.content


@pytest.mark.asyncio
async def test_reviews_filter_by_type(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Sys Review", status="active",
        metadata_={"next_review_due": yesterday},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.DEVICE, name="Dev Review", status="active",
        metadata_={"next_review_due": yesterday},
    )

    resp = await auth_client.get("/reviews?type=system")
    assert b"Sys Review" in resp.content
    assert b"Dev Review" not in resp.content


@pytest.mark.asyncio
async def test_reviews_filter_by_status(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    in_90_days = (date.today() + timedelta(days=90)).isoformat()
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Overdue Sys", status="active",
        metadata_={"next_review_due": yesterday},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="OK Sys", status="active",
        metadata_={"next_review_due": in_90_days},
    )

    resp = await auth_client.get("/reviews?status=overdue")
    assert b"Overdue Sys" in resp.content
    assert b"OK Sys" not in resp.content


@pytest.mark.asyncio
async def test_dashboard_review_counts(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    in_10_days = (date.today() + timedelta(days=10)).isoformat()
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Overdue", status="active",
        metadata_={"next_review_due": yesterday},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.DEVICE, name="Soon", status="active",
        metadata_={"next_review_due": in_10_days},
    )

    resp = await auth_client.get("/")
    assert resp.status_code == 200
    assert b"1 overdue" in resp.content
    assert b"1 due soon" in resp.content


@pytest.mark.asyncio
async def test_risk_review_date_field(auth_client, pool):
    """Risk uses 'review_date' as its due date field."""
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="Overdue Risk", status="active",
        metadata_={"review_date": yesterday, "likelihood": "likely", "impact": "major"},
    )

    resp = await auth_client.get("/reviews")
    assert b"Overdue Risk" in resp.content


@pytest.mark.asyncio
async def test_requirement_due_date_field(auth_client, pool):
    """Requirement uses 'due_date' as its due date field."""
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    in_5_days = (date.today() + timedelta(days=5)).isoformat()
    await asset_svc.create_asset(
        pool, type=AssetType.REQUIREMENT, name="Due Requirement", status="active",
        metadata_={"due_date": in_5_days},
    )

    resp = await auth_client.get("/reviews")
    assert b"Due Requirement" in resp.content
    assert b"Due Soon" in resp.content


@pytest.mark.asyncio
async def test_review_counts_service(pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc
    from grcen.services.review_service import get_review_counts

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    in_10_days = (date.today() + timedelta(days=10)).isoformat()
    in_90_days = (date.today() + timedelta(days=90)).isoformat()

    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="R1", status="active",
        metadata_={"next_review_due": yesterday},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="R2", status="active",
        metadata_={"next_review_due": in_10_days},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="R3", status="active",
        metadata_={"next_review_due": in_90_days},
    )

    counts = await get_review_counts(pool)
    assert counts["overdue"] == 1
    assert counts["due_soon"] == 1
