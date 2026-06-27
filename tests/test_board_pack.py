"""Executive board pack: data gathering, narratives, branded PDF."""
import pytest

from grcen.models.asset import AssetType
from grcen.services import (
    asset as asset_svc,
    board_service,
    catalog_sync,
    organization_service,
    pdf_service,
)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _seed(pool, org):
    await catalog_sync.sync_catalog(pool, {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A",
                        "requirements": [{"ref": "fwa:R1", "name": "R1"}]}],
        "controls": [{"ref": "C1", "name": "C1", "satisfies": ["fwa:R1"]}],
    }, organization_id=org)
    await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.RISK, name="Big risk",
        metadata_={"residual_risk_score": 20, "likelihood": "almost_certain",
                   "impact": "catastrophic", "severity": "critical"})
    await asset_svc.create_asset(pool, organization_id=org, type=AssetType.INCIDENT,
                                 name="Inc", metadata_={"incident_status": "open"})


@pytest.mark.asyncio
async def test_gather_structure(pool):
    org = await _org(pool)
    await _seed(pool, org)
    data = await board_service.gather(pool, organization_id=org)
    assert data["risk"]["summary"]["total"] >= 1
    assert data["risk"]["top"][0]["name"] == "Big risk"
    assert len(data["compliance"]["frameworks"]) == 1
    assert data["compliance"]["frameworks"][0]["effective"] == 100
    assert data["operations"]["open_incidents"] == 1


@pytest.mark.asyncio
async def test_narratives_roundtrip(pool):
    org = await _org(pool)
    await board_service.set_narrative(
        pool, organization_id=org, period="2026-Q2", section="overview", body="Strong quarter.")
    got = await board_service.get_narratives(pool, organization_id=org, period="2026-Q2")
    assert got["overview"] == "Strong quarter."
    # different period is isolated
    assert await board_service.get_narratives(pool, organization_id=org, period="2026-Q1") == {}


@pytest.mark.asyncio
async def test_render_board_pack_pdf(pool):
    org = await _org(pool)
    await _seed(pool, org)
    pdf = await pdf_service.render_board_pack(pool, organization_id=org, period="2026-Q2")
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_executive_page_and_pdf_route(auth_client, pool):
    org = await _org(pool)
    await _seed(pool, org)

    page = await auth_client.get("/reports/executive")
    assert page.status_code == 200
    assert "Executive Board Pack" in page.text
    assert "FW A" in page.text

    # save a narrative
    resp = await auth_client.post(
        "/reports/executive",
        data={"period": "current", "section": "overview", "body": "Board summary text"},
        follow_redirects=False)
    assert resp.status_code == 302
    got = await board_service.get_narratives(pool, organization_id=org, period="current")
    assert got["overview"] == "Board summary text"

    pdf = await auth_client.get("/reports/board-pack.pdf?period=current")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"
