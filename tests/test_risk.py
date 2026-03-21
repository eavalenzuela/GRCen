"""Tests for risk scoring and heatmap."""

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
