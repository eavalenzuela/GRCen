"""Outbound vendor campaigns: lifecycle + login-less portal."""
import pytest

from grcen.services import organization_service, vendor_campaign_service as vc


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _campaign_with_questions(pool, org, *, name="Vendor X Assessment"):
    camp = await vc.create_campaign(pool, organization_id=org, name=name)
    await vc.add_question(pool, camp["id"], organization_id=org, text="Encrypt data at rest?")
    await vc.import_questions(
        pool, camp["id"], organization_id=org, questions=["MFA enforced?", "Pentest cadence?", ""])
    return camp


@pytest.mark.asyncio
async def test_create_mints_token(pool):
    org = await _org(pool)
    camp = await vc.create_campaign(pool, organization_id=org, name="ACME Review")
    assert camp["status"] == "draft"
    assert camp["access_token"] and len(camp["access_token"]) >= 20


@pytest.mark.asyncio
async def test_questions_and_progress(pool):
    org = await _org(pool)
    camp = await _campaign_with_questions(pool, org)
    qs = await vc.list_questions(pool, camp["id"])
    assert len(qs) == 3  # blank import row dropped
    assert vc.progress(qs) == (0, 3)


@pytest.mark.asyncio
async def test_save_answers_flips_status(pool):
    org = await _org(pool)
    camp = await _campaign_with_questions(pool, org)
    await vc.set_status(pool, camp["id"], "sent", organization_id=org)
    qs = await vc.list_questions(pool, camp["id"])
    await vc.save_answers(pool, camp["id"], {qs[0]["id"]: "Yes, AES-256", qs[1]["id"]: ""})
    qs2 = {q["id"]: q for q in await vc.list_questions(pool, camp["id"])}
    assert qs2[qs[0]["id"]]["status"] == "answered"
    assert qs2[qs[1]["id"]]["status"] == "unanswered"
    camp2 = await vc.get_campaign(pool, camp["id"], organization_id=org)
    assert camp2["status"] == "in_progress"  # sent → in_progress on first save


@pytest.mark.asyncio
async def test_get_by_token(pool):
    org = await _org(pool)
    camp = await _campaign_with_questions(pool, org)
    got = await vc.get_by_token(pool, camp["access_token"])
    assert got["id"] == camp["id"]
    assert got["org_name"]
    assert await vc.get_by_token(pool, "nope") is None


@pytest.mark.asyncio
async def test_portal_hidden_until_sent(client, pool):
    org = await _org(pool)
    camp = await _campaign_with_questions(pool, org)
    # draft → 404
    r = await client.get(f"/vendor-portal/{camp['access_token']}")
    assert r.status_code == 404
    # sent → visible
    await vc.set_status(pool, camp["id"], "sent", organization_id=org)
    r = await client.get(f"/vendor-portal/{camp['access_token']}")
    assert r.status_code == 200
    assert "Encrypt data at rest?" in r.text


@pytest.mark.asyncio
async def test_portal_submit_then_readonly(client, pool):
    org = await _org(pool)
    camp = await _campaign_with_questions(pool, org)
    await vc.set_status(pool, camp["id"], "sent", organization_id=org)
    token = camp["access_token"]
    qs = await vc.list_questions(pool, camp["id"])

    r = await client.post(
        f"/vendor-portal/{token}",
        data={f"answer_{qs[0]['id']}": "AES-256 everywhere", "action": "submit"},
        follow_redirects=False)
    assert r.status_code == 302
    camp2 = await vc.get_campaign(pool, camp["id"], organization_id=org)
    assert camp2["status"] == "submitted"
    answered = {q["id"]: q for q in await vc.list_questions(pool, camp["id"])}
    assert answered[qs[0]["id"]]["answer"] == "AES-256 everywhere"

    # read-only afterward: GET shows the thank-you, POST is rejected
    r = await client.get(f"/vendor-portal/{token}")
    assert r.status_code == 200
    assert "submitted" in r.text.lower()
    r = await client.post(
        f"/vendor-portal/{token}", data={"action": "submit"}, follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_internal_routes(auth_client, pool):
    r = await auth_client.get("/vendor-campaigns")
    assert r.status_code == 200
    r = await auth_client.post(
        "/vendor-campaigns", data={"name": "ACME Annual Review"}, follow_redirects=False)
    assert r.status_code == 302
    assert "/vendor-campaigns/" in r.headers["location"]
