"""Tests for risk bulk actions, trend snapshots, and control effectiveness rollup."""

import uuid
from datetime import date, timedelta

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import relationship as rel_svc
from grcen.services import risk_service as risk_svc
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _risk(pool, admin_id, name, **meta_extra):
    meta = {"likelihood": "likely", "impact": "major"}
    meta.update(meta_extra)
    return await asset_svc.create_asset(
        pool,
        type=AssetType.RISK,
        name=name,
        status="active",
        updated_by=admin_id,
        metadata_=meta,
    )


# ── bulk update ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_update_treatment_and_review(pool, admin_user):
    r1 = await _risk(pool, admin_user.id, "R1")
    r2 = await _risk(pool, admin_user.id, "R2")

    updated = await risk_svc.bulk_update_risks(
        pool,
        [r1.id, r2.id],
        treatment="mitigate",
        review_date="2026-12-31",
        updated_by=admin_user.id,
    )
    assert set(updated) == {r1.id, r2.id}

    for rid in (r1.id, r2.id):
        fresh = await asset_svc.get_asset(pool, rid)
        assert fresh.metadata_["treatment"] == "mitigate"
        assert fresh.metadata_["review_date"] == "2026-12-31"


@pytest.mark.asyncio
async def test_bulk_update_no_fields_is_noop(pool, admin_user):
    r = await _risk(pool, admin_user.id, "R")
    updated = await risk_svc.bulk_update_risks(pool, [r.id])
    assert updated == []


@pytest.mark.asyncio
async def test_bulk_update_preserves_other_metadata(pool, admin_user):
    r = await _risk(
        pool, admin_user.id, "R",
        risk_category="security", control_effectiveness="partially_effective",
    )
    await risk_svc.bulk_update_risks(
        pool, [r.id], treatment="accept", updated_by=admin_user.id
    )
    fresh = await asset_svc.get_asset(pool, r.id)
    assert fresh.metadata_["treatment"] == "accept"
    # Untouched keys still present
    assert fresh.metadata_["risk_category"] == "security"
    assert fresh.metadata_["control_effectiveness"] == "partially_effective"
    assert fresh.metadata_["likelihood"] == "likely"


@pytest.mark.asyncio
async def test_bulk_update_owner_id(pool, admin_user):
    owner = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Owner", status="active",
        updated_by=admin_user.id,
    )
    r = await _risk(pool, admin_user.id, "R")
    await risk_svc.bulk_update_risks(
        pool, [r.id], owner_id=owner.id, updated_by=admin_user.id
    )
    fresh = await asset_svc.get_asset(pool, r.id)
    assert fresh.owner_id == owner.id


@pytest.mark.asyncio
async def test_bulk_update_http_endpoint(auth_client, pool, admin_user):
    from tests.conftest import _extract_csrf_from_session_cookie

    r1 = await _risk(pool, admin_user.id, "R1")
    r2 = await _risk(pool, admin_user.id, "R2")
    resp = await auth_client.post(
        "/risk-management/bulk-update",
        data={
            "risk_ids": [str(r1.id), str(r2.id)],
            "treatment": "transfer",
            "review_date": "",
            "owner_id": "",
            "csrf_token": _extract_csrf_from_session_cookie(auth_client),
        },
    )
    assert resp.status_code in (302, 303)
    for rid in (r1.id, r2.id):
        fresh = await asset_svc.get_asset(pool, rid)
        assert fresh.metadata_["treatment"] == "transfer"


# ── trend snapshots ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_snapshot_is_idempotent(pool, admin_user):
    await _risk(pool, admin_user.id, "R1")
    snap1 = await risk_svc.capture_risk_snapshot(pool)
    snap2 = await risk_svc.capture_risk_snapshot(pool)
    assert snap1["snapshot_date"] == snap2["snapshot_date"]
    count = await pool.fetchval("SELECT count(*) FROM risk_snapshots")
    assert count == 1


@pytest.mark.asyncio
async def test_severity_trend_returns_deltas(pool, admin_user):
    # Insert a "yesterday" snapshot with 1 critical.
    yesterday = date.today() - timedelta(days=1)
    await pool.execute(
        """INSERT INTO risk_snapshots
               (snapshot_date, total, critical, high, medium, low, overdue, no_treatment)
           VALUES ($1, 1, 1, 0, 0, 0, 0, 0)""",
        yesterday,
    )
    # Today: add a new critical risk (likely × catastrophic = 20 = critical).
    await _risk(pool, admin_user.id, "R1", impact="catastrophic")

    trend = await risk_svc.get_severity_trend(pool)
    assert trend["prior"] is not None
    assert trend["prior"]["snapshot_date"] == yesterday
    assert trend["current"]["critical"] == 1
    # prior also had 1 critical → delta 0
    assert trend["deltas"]["critical"] == 0


@pytest.mark.asyncio
async def test_severity_trend_no_prior_returns_empty_deltas(pool, admin_user):
    await _risk(pool, admin_user.id, "R1")
    trend = await risk_svc.get_severity_trend(pool)
    assert trend["prior"] is None
    assert trend["deltas"] == {}


# ── control-effectiveness rollup ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollup_with_no_mitigators(pool, admin_user):
    r = await _risk(pool, admin_user.id, "R")
    rollup = await risk_svc.get_risk_control_rollup(pool, [r.id])
    assert rollup[r.id]["control_count"] == 0
    assert rollup[r.id]["mitigator_count"] == 0
    assert rollup[r.id]["effectiveness_label"] == "none"


@pytest.mark.asyncio
async def test_rollup_averages_control_effectiveness(pool, admin_user):
    r = await _risk(pool, admin_user.id, "R")
    c1 = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="C1", status="active",
        updated_by=admin_user.id,
        metadata_={"effectiveness": "effective"},
    )
    c2 = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="C2", status="active",
        updated_by=admin_user.id,
        metadata_={"effectiveness": "partially_effective"},
    )
    for ctrl in (c1, c2):
        await rel_svc.create_relationship(
            pool, source_asset_id=r.id, target_asset_id=ctrl.id,
            relationship_type="mitigated_by", description="",
        )
    rollup = await risk_svc.get_risk_control_rollup(pool, [r.id])
    assert rollup[r.id]["control_count"] == 2
    # (1.0 + 0.5) / 2 = 0.75 → adequate
    assert rollup[r.id]["score"] == 0.75
    assert rollup[r.id]["effectiveness_label"] == "adequate"


@pytest.mark.asyncio
async def test_rollup_distinguishes_controls_from_other_mitigators(pool, admin_user):
    r = await _risk(pool, admin_user.id, "R")
    policy = await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="P", status="active", updated_by=admin_user.id,
    )
    ctrl = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="C", status="active",
        updated_by=admin_user.id, metadata_={"effectiveness": "effective"},
    )
    for a in (policy, ctrl):
        await rel_svc.create_relationship(
            pool, source_asset_id=r.id, target_asset_id=a.id,
            relationship_type="mitigated_by", description="",
        )
    rollup = await risk_svc.get_risk_control_rollup(pool, [r.id])
    # 2 mitigators total, but only 1 is a control
    assert rollup[r.id]["mitigator_count"] == 2
    assert rollup[r.id]["control_count"] == 1
    assert rollup[r.id]["score"] == 1.0
    assert rollup[r.id]["effectiveness_label"] == "strong"


@pytest.mark.asyncio
async def test_rollup_labels_ineffective_as_none(pool, admin_user):
    r = await _risk(pool, admin_user.id, "R")
    ctrl = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="C", status="active",
        updated_by=admin_user.id, metadata_={"effectiveness": "ineffective"},
    )
    await rel_svc.create_relationship(
        pool, source_asset_id=r.id, target_asset_id=ctrl.id,
        relationship_type="mitigated_by", description="",
    )
    rollup = await risk_svc.get_risk_control_rollup(pool, [r.id])
    assert rollup[r.id]["score"] == 0.0
    assert rollup[r.id]["effectiveness_label"] == "none"


# ── page renders with new columns ────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_page_renders_bulk_controls_when_editor(auth_client, pool, admin_user):
    await _risk(pool, admin_user.id, "R1")
    resp = await auth_client.get("/risk-management")
    assert resp.status_code == 200
    assert "Bulk Apply to Selected" in resp.text
    # Arrow indicators may not show without a prior snapshot; just check page renders fresh
    assert "Risk Register" in resp.text


@pytest.mark.asyncio
async def test_risk_page_hides_bulk_controls_for_viewer(viewer_client, pool, admin_user):
    await _risk(pool, admin_user.id, "R1")
    resp = await viewer_client.get("/risk-management")
    assert resp.status_code == 200
    assert "Bulk Apply to Selected" not in resp.text
