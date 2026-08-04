"""Tests for risk scoring and heatmap."""

from datetime import date, timedelta

import pytest

from grcen.services.risk_service import (
    IMPACT_LEVELS,
    LIKELIHOOD_LEVELS,
    compute_risk_score,
    impact_value,
    likelihood_value,
    score_color,
)


# --- Unit tests for score computation ---


def test_likelihood_values():
    assert likelihood_value("rare") == 1
    assert likelihood_value("almost_certain") == 5
    assert likelihood_value("unknown") == 0


def test_impact_values():
    assert impact_value("insignificant") == 1
    assert impact_value("catastrophic") == 5
    assert impact_value("unknown") == 0


def test_compute_risk_score():
    assert compute_risk_score("rare", "insignificant") == 1
    assert compute_risk_score("almost_certain", "catastrophic") == 25
    assert compute_risk_score("likely", "moderate") == 12
    assert compute_risk_score(None, "moderate") is None
    assert compute_risk_score("likely", None) is None
    assert compute_risk_score("invalid", "moderate") is None


def test_score_color():
    assert score_color(1) == "low"
    assert score_color(5) == "low"
    assert score_color(6) == "medium"
    assert score_color(11) == "medium"
    assert score_color(12) == "high"
    assert score_color(19) == "high"
    assert score_color(20) == "critical"
    assert score_color(25) == "critical"


# --- Integration tests ---


@pytest.mark.asyncio
async def test_risk_heatmap_on_dashboard(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    # Create a risk asset with likelihood and impact
    await asset_svc.create_asset(
        pool,
        type=AssetType.RISK,
        name="Test Risk",
        status="active",
        metadata_={"likelihood": "likely", "impact": "major"},
    )

    resp = await auth_client.get("/")
    assert resp.status_code == 200
    assert b"Risk Heatmap" in resp.content
    assert b"Test Risk" in resp.content  # should appear in top risks


@pytest.mark.asyncio
async def test_risk_score_auto_computed_on_create(auth_client, pool):
    resp = await auth_client.post(
        "/assets/new",
        data={
            "type": "risk",
            "name": "Auto Score Risk",
            "status": "active",
            "owner": "",
            "description": "",
            "metadata.likelihood": "likely",
            "metadata.impact": "major",
            "metadata.risk_category": "security",
        },
    )
    assert resp.status_code == 302

    # Fetch the created asset and check the auto-computed score
    row = await pool.fetchrow(
        "SELECT metadata FROM assets WHERE name = 'Auto Score Risk'"
    )
    import json
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    # likely=4 x major=4 = 16
    assert meta["inherent_risk_score"] == 16


@pytest.mark.asyncio
async def test_risk_score_auto_computed_on_update(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    asset = await asset_svc.create_asset(
        pool,
        type=AssetType.RISK,
        name="Update Score Risk",
        status="active",
        metadata_={"likelihood": "rare", "impact": "minor"},
    )

    resp = await auth_client.post(
        f"/assets/{asset.id}/edit",
        data={
            "name": "Update Score Risk",
            "status": "active",
            "owner": "",
            "description": "",
            "metadata.likelihood": "almost_certain",
            "metadata.impact": "catastrophic",
            "metadata.risk_category": "",
        },
    )
    assert resp.status_code == 302

    import json
    row = await pool.fetchrow("SELECT metadata FROM assets WHERE id = $1", asset.id)
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    # almost_certain=5 x catastrophic=5 = 25
    assert meta["inherent_risk_score"] == 25


@pytest.mark.asyncio
async def test_heatmap_data(pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc
    from grcen.services.risk_service import get_risk_heatmap

    await asset_svc.create_asset(
        pool,
        type=AssetType.RISK,
        name="Heatmap Risk 1",
        status="active",
        metadata_={"likelihood": "possible", "impact": "moderate"},
    )
    await asset_svc.create_asset(
        pool,
        type=AssetType.RISK,
        name="Heatmap Risk 2",
        status="active",
        metadata_={"likelihood": "possible", "impact": "moderate"},
    )

    heatmap = await get_risk_heatmap(pool)
    # possible=3, moderate=3
    cell = heatmap.get((3, 3), [])
    assert len(cell) == 2
    names = {r["name"] for r in cell}
    assert names == {"Heatmap Risk 1", "Heatmap Risk 2"}


@pytest.mark.asyncio
async def test_top_risks_sorted(pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc
    from grcen.services.risk_service import get_top_risks

    await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="Low Risk", status="active",
        metadata_={"likelihood": "rare", "impact": "insignificant"},
    )
    await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="High Risk", status="active",
        metadata_={"likelihood": "almost_certain", "impact": "catastrophic"},
    )

    top = await get_top_risks(pool)
    assert top[0]["name"] == "High Risk"
    assert top[0]["score"] == 25
    assert top[1]["name"] == "Low Risk"
    assert top[1]["score"] == 1


@pytest.mark.asyncio
async def test_risk_register_mixed_scored_and_unscored_does_not_crash(pool):
    """Regression for the register 500: a scored risk (score int) alongside an
    unscored one (score 0) must not crash the sort with a str-vs-int TypeError."""
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc
    from grcen.services.risk_service import get_risk_register

    await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="Scored Risk", status="active",
        metadata_={"likelihood": "likely", "impact": "major"},  # score 16
    )
    await asset_svc.create_asset(
        pool, type=AssetType.RISK, name="Unscored Risk", status="active",
        metadata_={},  # no likelihood/impact -> score 0
    )

    # Default sort is by score (numeric) — must not raise on the mixed set.
    by_score = await get_risk_register(pool)
    assert {r["name"] for r in by_score} == {"Scored Risk", "Unscored Risk"}
    assert by_score[0]["name"] == "Scored Risk"  # 16 before 0, desc

    # The text-column sort path must also tolerate the mix.
    by_name = await get_risk_register(pool, sort="name", order="asc")
    assert [r["name"] for r in by_name] == ["Scored Risk", "Unscored Risk"]


# --- only "Next Review Due" can be overdue --------------------------------


@pytest.mark.asyncio
async def test_last_reviewed_alone_does_not_mark_risk_overdue(pool):
    """"Last Reviewed" is in the past by definition.

    Falling back to it meant a risk reviewed yesterday read as overdue forever
    — the better maintained the risk, the more certainly it was flagged.
    """
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc, organization_service, risk_service

    org = await organization_service.get_default_org_id(pool)
    reviewed_yesterday = (date.today() - timedelta(days=1)).isoformat()

    diligent = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name="Diligently reviewed",
        status="active",
        metadata_={"likelihood": "3", "impact": "3",
                   "last_reviewed": reviewed_yesterday},
    )
    genuinely_due = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name="Actually overdue",
        status="active",
        metadata_={"likelihood": "3", "impact": "3",
                   "last_reviewed": reviewed_yesterday,
                   "review_date": (date.today() - timedelta(days=5)).isoformat()},
    )

    register = await risk_service.get_risk_register(pool, organization_id=org)
    by_id = {r["id"]: r for r in register}

    assert by_id[diligent.id]["is_overdue"] is False
    assert by_id[genuinely_due.id]["is_overdue"] is True


@pytest.mark.asyncio
async def test_future_review_date_is_not_overdue(pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc, organization_service, risk_service

    org = await organization_service.get_default_org_id(pool)
    r = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name="Future review",
        status="active",
        metadata_={"likelihood": "2", "impact": "2",
                   "review_date": (date.today() + timedelta(days=30)).isoformat()},
    )

    register = await risk_service.get_risk_register(pool, organization_id=org)
    by_id = {x["id"]: x for x in register}
    assert by_id[r.id]["is_overdue"] is False


@pytest.mark.asyncio
async def test_bulk_update_preserves_unrelated_metadata(pool):
    """Bulk update was a read-modify-write of the whole metadata blob.

    Anything changed between the read and the write — another bulk update over
    an overlapping selection, or an edit to an unrelated key — was reverted.
    """
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc, organization_service, risk_service

    org = await organization_service.get_default_org_id(pool)
    risk = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name="Merge me",
        status="active",
        metadata_={"likelihood": "3", "impact": "4", "category": "operational"},
    )

    await risk_service.bulk_update_risks(
        pool, [risk.id], treatment="mitigate", organization_id=org
    )

    row = await pool.fetchrow("SELECT metadata FROM assets WHERE id = $1", risk.id)
    meta = row["metadata"]
    if isinstance(meta, str):
        import json as _json
        meta = _json.loads(meta)
    assert meta["treatment"] == "mitigate"
    # The keys this call never mentioned must survive.
    assert meta["likelihood"] == "3"
    assert meta["impact"] == "4"
    assert meta["category"] == "operational"
