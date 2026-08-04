"""Tests for vulnerability-risk aggregation (roadmap I4)."""
import json

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc
from grcen.services import organization_service, risk_aggregation, vuln_sync


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _system(pool, org, name="Prod Web", hostname="web01"):
    return await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name=name,
        metadata_={"hostname": hostname}, organization_id=org)


async def _sync(pool, org, findings):
    # These tests drive the backlog to genuinely zero to check the "everything
    # resolved" rollup, which is exactly what allow_empty is for — an empty
    # export is otherwise refused, since it usually means a broken scanner.
    return await vuln_sync.sync_findings(
        pool, {"findings": findings}, organization_id=org,
        allow_empty=not findings,
    )


def _f(fid, sev, host=None, **over):
    f = {"finding_id": fid, "title": f"{fid} {sev}", "severity": sev}
    if host:
        f["affects"] = {"hostname": host}
    f.update(over)
    return f


async def _risk(pool, org):
    return await pool.fetchrow(
        "SELECT id, name, metadata, status FROM assets "
        "WHERE source = 'vuln-agg' AND type = 'risk' AND organization_id = $1",
        org)


def _meta(row):
    m = row["metadata"]
    return json.loads(m) if isinstance(m, str) else m


# ── system grouping ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregates_open_findings_into_a_system_risk(pool):
    org = await _org(pool)
    system = await _system(pool, org)
    await _sync(pool, org, [
        _f("F1", "critical", "web01", cve_id="CVE-1", cvss_score="9.8"),
        _f("F2", "medium", "web01", cve_id="CVE-2"),
    ])
    result = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert not result.errors
    assert result.risks_created == 1
    assert result.threatens_edges == 1
    assert result.relates_edges == 2
    assert result.findings_considered == 2

    risk = await _risk(pool, org)
    assert "Prod Web" in risk["name"]
    meta = _meta(risk)
    assert meta["severity"] == "critical"          # worst of the two
    assert meta["likelihood"] == "likely"
    assert meta["impact"] == "catastrophic"        # 4×5 = 20 → critical band
    assert meta["finding_count"] == 2
    assert meta["risk_category"] == "security"

    # Risk --threatens--> System
    edge = await pool.fetchrow(
        "SELECT source_asset_id, target_asset_id FROM relationships "
        "WHERE source = 'vuln-agg' AND relationship_type = 'threatens'")
    assert edge["source_asset_id"] == risk["id"]
    assert edge["target_asset_id"] == system.id


@pytest.mark.asyncio
async def test_resync_is_idempotent(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [_f("F1", "high", "web01")])
    await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    again = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert again.risks_created == 0
    assert again.risks_updated == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'vuln-agg' AND type = 'risk'") == 1


@pytest.mark.asyncio
async def test_remediated_finding_prunes_its_relates_edge(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [_f("F1", "critical", "web01"), _f("F2", "medium", "web01")])
    await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    # F2 drops out of the scan → vuln_sync archives it as remediated
    await _sync(pool, org, [_f("F1", "critical", "web01")])
    result = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert result.relates_edges == 1   # only F1 remains
    assert result.edges_pruned == 1    # F2's relates edge pruned
    assert (await _risk(pool, org))["status"] == "active"


@pytest.mark.asyncio
async def test_all_findings_resolved_archives_the_risk(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [_f("F1", "critical", "web01")])
    await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    await _sync(pool, org, [])  # F1 remediated → archived
    result = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert result.risks_archived == 1
    assert (await _risk(pool, org))["status"] == "archived"


@pytest.mark.asyncio
async def test_archived_risk_reactivates_on_new_finding(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [_f("F1", "critical", "web01")])
    await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    await _sync(pool, org, [])  # resolve F1
    await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)  # archives risk
    assert (await _risk(pool, org))["status"] == "archived"

    await _sync(pool, org, [_f("F2", "high", "web01")])  # new vuln, same host
    result = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert result.risks_updated == 1  # same source_ref → reactivated in place
    assert (await _risk(pool, org))["status"] == "active"


@pytest.mark.asyncio
async def test_unassigned_findings_counted_not_risked(pool):
    org = await _org(pool)
    await _sync(pool, org, [_f("F1", "critical")])  # no affects → no system
    result = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert result.unassigned == 1
    assert result.risks_created == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [_f("F1", "high", "web01")])
    result = await risk_aggregation.aggregate_vuln_risks(
        pool, organization_id=org, dry_run=True)
    assert result.risks_created == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'vuln-agg'") == 0


# ── severity grouping ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_severity_grouping(pool):
    org = await _org(pool)
    await _system(pool, org)
    await _sync(pool, org, [
        _f("F1", "critical", "web01"),
        _f("F2", "high", "web01"),
        _f("F3", "high"),   # no system, still counts by severity
    ])
    result = await risk_aggregation.aggregate_vuln_risks(
        pool, organization_id=org, group_by="severity")
    assert result.group_by == "severity"
    assert result.risks_created == 2   # critical + high buckets
    assert result.threatens_edges == 0  # no system edges in severity mode
    names = {r["name"] for r in await pool.fetch(
        "SELECT name FROM assets WHERE source = 'vuln-agg' AND type = 'risk'")}
    assert "Critical vulnerabilities" in names
    assert "High vulnerabilities" in names


@pytest.mark.asyncio
async def test_bad_group_by_rejected(pool):
    org = await _org(pool)
    result = await risk_aggregation.aggregate_vuln_risks(
        pool, organization_id=org, group_by="planet")
    assert result.errors
