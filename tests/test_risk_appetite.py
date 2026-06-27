"""Risk appetite: thresholds, per-risk evaluation, breach summary, UI/API."""
import pytest

from grcen.models.asset import AssetType
from grcen.services import appetite_service, asset as asset_svc, organization_service


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _risk(pool, org, *, name, residual=None, category=None, likelihood=None, impact=None):
    meta = {}
    if residual is not None:
        meta["residual_risk_score"] = residual
    if category:
        meta["risk_category"] = category
    if likelihood:
        meta["likelihood"] = likelihood
    if impact:
        meta["impact"] = impact
    a = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name=name, metadata_=meta)
    return a.id


async def _set(pool, org, cat, mx, wn):
    await appetite_service.set_appetite(
        pool, organization_id=org, risk_category=cat, max_score=mx, warn_score=wn)


@pytest.mark.asyncio
async def test_set_get_appetite(pool):
    org = await _org(pool)
    await _set(pool, org, "", 12, 6)
    await _set(pool, org, "security", 4, 2)
    app = await appetite_service.get_appetite(pool, organization_id=org)
    assert app[""] == {"max_score": 12, "warn_score": 6}
    assert app["security"] == {"max_score": 4, "warn_score": 2}


@pytest.mark.asyncio
async def test_evaluate_within_near_out(pool):
    org = await _org(pool)
    await _set(pool, org, "", 12, 6)
    await _risk(pool, org, name="out-risk", residual=20)
    await _risk(pool, org, name="near-risk", residual=10)
    await _risk(pool, org, name="within-risk", residual=4)
    rows = await appetite_service.evaluate_risks(pool, organization_id=org)
    evals = {e["name"]: e["status"] for e in rows}
    assert evals == {"out-risk": "out", "near-risk": "near", "within-risk": "within"}


@pytest.mark.asyncio
async def test_category_band_overrides_default(pool):
    org = await _org(pool)
    await _set(pool, org, "", 12, 6)
    await _set(pool, org, "security", 4, 2)
    await _risk(pool, org, name="sec-risk", residual=8, category="security")  # 8 > 4 → out
    rows = await appetite_service.evaluate_risks(pool, organization_id=org)
    evals = {e["name"]: e["status"] for e in rows}
    assert evals["sec-risk"] == "out"


@pytest.mark.asyncio
async def test_no_band_is_unknown(pool):
    org = await _org(pool)
    await _risk(pool, org, name="r", residual=20)
    evals = await appetite_service.evaluate_risks(pool, organization_id=org)
    assert evals[0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_residual_preferred_over_computed(pool):
    org = await _org(pool)
    await _set(pool, org, "", 12, 6)
    # likelihood/impact would compute low, but residual says 20 → out
    await _risk(pool, org, name="r", residual=20, likelihood="unlikely", impact="minor")
    evals = await appetite_service.evaluate_risks(pool, organization_id=org)
    assert evals[0]["score"] == 20
    assert evals[0]["status"] == "out"


@pytest.mark.asyncio
async def test_breach_summary(pool):
    org = await _org(pool)
    await _set(pool, org, "", 12, 6)
    await _risk(pool, org, name="out1", residual=20)
    await _risk(pool, org, name="near1", residual=10)
    summary = await appetite_service.breach_summary(pool, organization_id=org)
    assert summary["out"] == 1
    assert summary["near"] == 1
    assert summary["out_risks"][0]["name"] == "out1"


@pytest.mark.asyncio
async def test_api_and_admin_page_and_banner(auth_client, pool):
    org = await _org(pool)
    await _risk(pool, org, name="big-risk", residual=20)

    # admin sets a band via the page
    resp = await auth_client.post(
        "/admin/risk-appetite",
        data={"risk_category": "", "max_score": "12", "warn_score": "6"},
        follow_redirects=False)
    assert resp.status_code == 302
    assert (await appetite_service.get_appetite(pool, organization_id=org))[""]["max_score"] == 12

    # API reports the breach
    api = (await auth_client.get("/api/risk-appetite")).json()
    assert api["summary"]["out"] == 1

    # risk-management page shows the banner
    page = await auth_client.get("/risk-management")
    assert page.status_code == 200
    assert "out of appetite" in page.text
