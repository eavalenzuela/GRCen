"""Gap CSV export, control library, last-audited rollups."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc
from grcen.services import framework_service
from grcen.services import relationship as rel_svc


@pytest.fixture
async def small_framework(pool):
    """Build: Framework F → Req1, Req2, Req3.
    Req1 satisfied by control C1 (via 'satisfies' inbound).
    Req2 satisfied by policy P1 (via 'satisfied_by' outbound).
    Req3 is a gap.
    """
    fw = await asset_svc.create_asset(pool, type=AssetType.FRAMEWORK, name="F")
    r1 = await asset_svc.create_asset(pool, type=AssetType.REQUIREMENT, name="Req1")
    r2 = await asset_svc.create_asset(pool, type=AssetType.REQUIREMENT, name="Req2")
    r3 = await asset_svc.create_asset(pool, type=AssetType.REQUIREMENT, name="Req3")
    c1 = await asset_svc.create_asset(pool, type=AssetType.CONTROL, name="C1")
    p1 = await asset_svc.create_asset(pool, type=AssetType.POLICY, name="P1")
    for req in (r1, r2, r3):
        await rel_svc.create_relationship(
            pool, source_asset_id=fw.id, target_asset_id=req.id,
            relationship_type="parent_of",
        )
    await rel_svc.create_relationship(
        pool, source_asset_id=c1.id, target_asset_id=r1.id, relationship_type="satisfies",
    )
    await rel_svc.create_relationship(
        pool, source_asset_id=r2.id, target_asset_id=p1.id, relationship_type="satisfied_by",
    )
    return {"fw": fw, "r1": r1, "r2": r2, "r3": r3, "c1": c1, "p1": p1}


@pytest.mark.asyncio
async def test_gap_report_rows_marks_satisfied_and_gaps(pool, small_framework):
    fx = small_framework
    rows = await framework_service.gap_report_rows(pool, fx["fw"].id)
    by_name = {r["requirement_name"]: r for r in rows}
    assert by_name["Req1"]["satisfied"] == "yes"
    assert "C1 (control) via satisfies" in by_name["Req1"]["satisfiers"]
    assert by_name["Req2"]["satisfied"] == "yes"
    assert "P1 (policy) via satisfied_by" in by_name["Req2"]["satisfiers"]
    assert by_name["Req3"]["satisfied"] == "no"
    assert by_name["Req3"]["satisfier_count"] == 0


@pytest.mark.asyncio
async def test_gap_report_404_when_framework_not_found(pool):
    rows = await framework_service.gap_report_rows(pool, uuid.uuid4())
    assert rows == []


@pytest.mark.asyncio
async def test_gap_report_csv_endpoint(pool, auth_client, small_framework):
    fw_id = small_framework["fw"].id
    resp = await auth_client.get(f"/frameworks/{fw_id}/gap-report.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert body.startswith("requirement_id,requirement_name,satisfied,")
    assert "Req1,yes" in body
    assert "Req3,no" in body


@pytest.mark.asyncio
async def test_gap_report_csv_records_export(pool, auth_client, small_framework):
    fw_id = small_framework["fw"].id
    before = await pool.fetchval(
        "SELECT count(*) FROM data_access_log WHERE entity_type = 'framework' AND action = 'export'"
    )
    await auth_client.get(f"/frameworks/{fw_id}/gap-report.csv")
    after = await pool.fetchval(
        "SELECT count(*) FROM data_access_log WHERE entity_type = 'framework' AND action = 'export'"
    )
    assert after == before + 1


@pytest.mark.asyncio
async def test_control_library_lists_each_control(pool, small_framework):
    rows = await framework_service.list_controls_with_coverage(pool)
    by_name = {r["name"]: r for r in rows}
    assert "C1" in by_name
    req_names = {r["name"] for r in by_name["C1"]["requirements"]}
    assert "Req1" in req_names


@pytest.mark.asyncio
async def test_controls_page_renders(auth_client, small_framework):
    resp = await auth_client.get("/controls")
    assert resp.status_code == 200
    assert "Control Library" in resp.text
    assert "C1" in resp.text
    assert "Req1" in resp.text


@pytest.mark.asyncio
async def test_last_audited_pulls_from_audit_log(pool, small_framework):
    """Updating a satisfier writes an audit row; the rollup picks it up."""
    fx = small_framework
    # Run the regular update path so the audit_log gets a row.
    from grcen.services import audit_service
    await audit_service.log_audit_event(
        pool, user_id=None, username="tester", action="update",
        entity_type="asset", entity_id=fx["c1"].id, entity_name="C1",
    )
    rollup = await framework_service._last_audited_for_requirements(
        pool, [fx["r1"].id, fx["r3"].id]
    )
    assert rollup[fx["r1"].id] is not None  # via control C1's audit row
    assert rollup[fx["r3"].id] is None  # gap requirement, no satisfier
