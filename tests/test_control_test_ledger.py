"""Control test ledger: recording, write-back, cadence, overdue, continuity."""
from datetime import date, timedelta

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc, control_test_service, organization_service


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _control(pool, org, *, name="C1", metadata=None):
    a = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.CONTROL, name=name,
        metadata_=metadata or {"frequency": "quarterly"},
    )
    return a.id


async def _meta(pool, control_id):
    import json
    raw = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", control_id)
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.asyncio
async def test_record_rolls_result_onto_control(pool):
    org = await _org(pool)
    cid = await _control(pool, org, metadata={"frequency": "quarterly"})
    run = await control_test_service.record_test_run(
        pool, cid, result="pass", organization_id=org, notes="Q1 test"
    )
    assert run["result"] == "pass"
    meta = await _meta(pool, cid)
    assert meta["effectiveness"] == "effective"
    assert meta["last_tested"] == date.today().isoformat()
    assert meta["next_test_due"] == (date.today() + timedelta(days=91)).isoformat()


@pytest.mark.asyncio
async def test_result_to_effectiveness_mapping(pool):
    org = await _org(pool)
    for result, eff in [("fail", "ineffective"), ("partial", "partially_effective")]:
        cid = await _control(pool, org, name=f"C-{result}")
        await control_test_service.record_test_run(pool, cid, result=result, organization_id=org)
        assert (await _meta(pool, cid))["effectiveness"] == eff


@pytest.mark.asyncio
async def test_invalid_result_rejected(pool):
    org = await _org(pool)
    cid = await _control(pool, org)
    with pytest.raises(ValueError):
        await control_test_service.record_test_run(pool, cid, result="bogus", organization_id=org)


@pytest.mark.asyncio
async def test_history_newest_first(pool):
    org = await _org(pool)
    cid = await _control(pool, org)
    await control_test_service.record_test_run(pool, cid, result="fail", organization_id=org)
    await control_test_service.record_test_run(pool, cid, result="pass", organization_id=org)
    runs = await control_test_service.list_test_runs(pool, cid, organization_id=org)
    assert len(runs) == 2
    assert runs[0]["result"] == "pass"  # newest first
    sparks = await control_test_service.recent_results(pool, [cid], organization_id=org)
    assert sparks[cid] == ["fail", "pass"]  # oldest→newest


@pytest.mark.asyncio
async def test_overdue_for_test(pool):
    org = await _org(pool)
    await _control(pool, org, name="never-tested", metadata={"frequency": "quarterly"})
    await _control(pool, org, name="stale", metadata={
        "frequency": "quarterly", "last_tested": "2020-01-01", "next_test_due": "2020-04-01"})
    fresh = await _control(pool, org, name="fresh", metadata={"frequency": "quarterly"})
    await control_test_service.record_test_run(pool, fresh, result="pass", organization_id=org)

    rows = await control_test_service.overdue_for_test(pool, organization_id=org)
    overdue = {r["name"] for r in rows}
    assert "never-tested" in overdue
    assert "stale" in overdue
    assert "fresh" not in overdue  # just tested → next due in the future


@pytest.mark.asyncio
async def test_operated_continuously(pool):
    org = await _org(pool)
    cid = await _control(pool, org)
    await control_test_service.record_test_run(
        pool, cid, result="pass", organization_id=org,
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
    )
    assert await control_test_service.operated_continuously(
        pool, cid, start=date(2026, 1, 15), end=date(2026, 3, 1), organization_id=org)
    assert not await control_test_service.operated_continuously(
        pool, cid, start=date(2026, 1, 15), end=date(2026, 6, 1), organization_id=org)


@pytest.mark.asyncio
async def test_api_record_list_overdue(auth_client, pool):
    org = await _org(pool)
    cid = await _control(pool, org, metadata={"frequency": "monthly"})

    resp = await auth_client.post(
        f"/api/controls/{cid}/test-runs", json={"result": "pass", "notes": "ok"})
    assert resp.status_code == 201
    assert resp.json()["result"] == "pass"

    bad = await auth_client.post(f"/api/controls/{cid}/test-runs", json={"result": "nope"})
    assert bad.status_code == 400

    runs = (await auth_client.get(f"/api/controls/{cid}/test-runs")).json()
    assert len(runs) == 1 and runs[0]["result"] == "pass"

    over = await _control(pool, org, name="overdue-ctl", metadata={"frequency": "monthly"})
    overdue = (await auth_client.get("/api/controls/overdue")).json()
    assert str(over) in [r["id"] for r in overdue]


@pytest.mark.asyncio
async def test_controls_page_renders_and_records(auth_client, pool):
    org = await _org(pool)
    cid = await _control(pool, org, name="ui-control", metadata={"frequency": "monthly"})

    page = await auth_client.get("/controls")
    assert page.status_code == 200
    assert "ui-control" in page.text
    assert "overdue for testing" in page.text  # the never-tested control triggers the banner

    resp = await auth_client.post(
        f"/controls/{cid}/test", data={"result": "pass"}, follow_redirects=False)
    assert resp.status_code == 302
    assert "Recorded%20pass" in resp.headers["location"]
    assert (await _meta(pool, cid))["effectiveness"] == "effective"
