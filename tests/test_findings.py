"""Findings register: asset type, audit rollup, overdue, gated CAPA closure."""
from datetime import date, timedelta

import pytest

from grcen.models.asset import AssetType
from grcen.services import (
    asset as asset_svc,
    findings_service,
    organization_service,
    relationship as rel_svc,
)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _finding(pool, org, *, name="F", status="open", due=None, owner_id=None, cap=""):
    meta = {"finding_status": status, "severity": "high", "corrective_action_plan": cap}
    if due:
        meta["due_date"] = due
    a = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.FINDING, name=name,
        owner_id=owner_id, metadata_=meta)
    return a.id


def test_finding_is_a_register():
    from grcen.registers import REGISTERS
    reg = REGISTERS[AssetType.FINDING]
    assert reg.slug == "findings"
    assert reg.lifecycle_column == "meta.finding_status"


@pytest.mark.asyncio
async def test_audit_finding_rollup(pool):
    org = await _org(pool)
    audit = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.AUDIT, name="Q1 Audit")
    f_open = await _finding(pool, org, name="open-f", status="open")
    f_closed = await _finding(pool, org, name="closed-f", status="closed")
    await rel_svc.create_relationship(pool, organization_id=org, source_asset_id=f_open,
                                      target_asset_id=audit.id, relationship_type="raised_by")
    await rel_svc.create_relationship(pool, organization_id=org, source_asset_id=f_closed,
                                      target_asset_id=audit.id, relationship_type="raised_by")
    rollup = await findings_service.audit_finding_rollup(pool, [audit.id])
    assert rollup[audit.id] == {"open": 1, "total": 2}


@pytest.mark.asyncio
async def test_overdue_findings(pool):
    org = await _org(pool)
    past = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()
    await _finding(pool, org, name="late", status="open", due=past)
    await _finding(pool, org, name="ontime", status="open", due=future)
    await _finding(pool, org, name="late-but-closed", status="closed", due=past)
    rows = await findings_service.overdue_findings(pool, organization_id=org)
    assert {r["name"] for r in rows} == {"late"}


@pytest.mark.asyncio
async def test_close_requires_cap_and_independent_verification(pool):
    org = await _org(pool)
    alice = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.PERSON, name="Alice")
    fid = await _finding(pool, org, name="F1", owner_id=alice.id, cap="")

    # no corrective action plan → blocked
    with pytest.raises(ValueError, match="corrective action plan"):
        await findings_service.close_finding(pool, fid, verified_by="Bob", organization_id=org)

    # add a CAP, but the owner can't self-verify
    await asset_svc.update_asset(
        pool, fid, organization_id=org,
        metadata_={"finding_status": "open", "corrective_action_plan": "patched"})
    with pytest.raises(ValueError, match="independent verification"):
        await findings_service.close_finding(pool, fid, verified_by="Alice", organization_id=org)

    # an independent verifier closes it
    meta = await findings_service.close_finding(pool, fid, verified_by="Bob", organization_id=org)
    assert meta["finding_status"] == "closed"
    assert meta["verified_by"] == "Bob"
    assert meta["verified_at"] == date.today().isoformat()


@pytest.mark.asyncio
async def test_api_close_and_overdue(auth_client, pool):
    org = await _org(pool)
    past = (date.today() - timedelta(days=2)).isoformat()
    fid = await _finding(pool, org, name="api-f", status="open", due=past, cap="remediated")

    overdue = (await auth_client.get("/api/findings/overdue")).json()
    assert str(fid) in [r["id"] for r in overdue]

    resp = await auth_client.post(f"/api/findings/{fid}/close", json={"verified_by": "Reviewer"})
    assert resp.status_code == 201 or resp.status_code == 200
    assert resp.json()["finding_status"] == "closed"


@pytest.mark.asyncio
async def test_findings_register_page(auth_client, pool):
    org = await _org(pool)
    await _finding(pool, org, name="visible-finding", status="open")
    page = await auth_client.get("/assets?type=finding")
    assert page.status_code == 200
    assert "visible-finding" in page.text
