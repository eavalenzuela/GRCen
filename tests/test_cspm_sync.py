"""Tests for the CSPM misconfiguration sync (roadmap I4 second wave)."""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import cspm_sync, organization_service
from grcen.services.auth import create_user
from grcen.services.token_service import create_token

_ARN = "arn:aws:s3:::my-bucket"


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _system(pool, org, name="Prod Bucket", **meta):
    return await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name=name, metadata_=meta, organization_id=org)


def _export(*findings, source=None):
    e = {"findings": list(findings)}
    if source is not None:
        e["source"] = source
    return e


def _f(fid, title, resource=None, **over):
    f = {"finding_id": fid, "title": title, "severity": over.pop("severity", "high")}
    if resource is not None:
        f["resource"] = resource
    f.update(over)
    return f


async def _finding(pool, org, src="cspm"):
    return await pool.fetchrow(
        "SELECT id, metadata, status FROM assets "
        "WHERE source = $1 AND type = 'finding' AND organization_id = $2", src, org)


def _meta(row):
    m = row["metadata"]
    return json.loads(m) if isinstance(m, str) else m


# ── resolution ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolves_by_cloud_resource_id_and_maps_fields(pool):
    org = await _org(pool)
    system = await _system(pool, org, cloud_resource_id=_ARN)
    result = await cspm_sync.sync_findings(
        pool,
        _export(_f("W1", "S3 bucket public", severity="high", check_id="CIS-2.1.5",
                   framework="CIS AWS 1.4", provider="aws",
                   resource={"resource_id": _ARN, "name": "my-bucket", "type": "AWS::S3::Bucket"}),
                source="cspm:wiz"),
        organization_id=org)
    assert not result.errors
    assert result.source == "cspm:wiz"
    assert result.findings_created == 1
    assert result.affects_edges == 1

    fnd = await _finding(pool, org, "cspm:wiz")
    meta = _meta(fnd)
    assert meta["scan_id"] == "CIS-2.1.5"
    assert meta["scanner"] == "aws"
    assert meta["cspm_framework"] == "CIS AWS 1.4"
    assert meta["severity"] == "high"
    assert meta["source"] == "automated"
    assert meta["affected_asset"] == "my-bucket"

    edge = await pool.fetchrow(
        "SELECT source_asset_id, target_asset_id FROM relationships "
        "WHERE source = 'cspm:wiz' AND source_ref = 'affects:W1'")
    assert edge["source_asset_id"] == system.id
    assert edge["target_asset_id"] == fnd["id"]


@pytest.mark.asyncio
async def test_resolves_by_external_id(pool):
    org = await _org(pool)
    await _system(pool, org, external_id=_ARN)  # CMDB CI id happens to be the ARN
    result = await cspm_sync.sync_findings(
        pool, _export(_f("W1", "x", resource={"resource_id": _ARN})), organization_id=org)
    assert result.affects_edges == 1


@pytest.mark.asyncio
async def test_resolves_by_name(pool):
    org = await _org(pool)
    await _system(pool, org, name="my-bucket")  # no cloud ids, name match
    result = await cspm_sync.sync_findings(
        pool, _export(_f("W1", "x", resource={"name": "my-bucket"})), organization_id=org)
    assert result.affects_edges == 1


@pytest.mark.asyncio
async def test_unresolved_resource_warns_but_creates_finding(pool):
    org = await _org(pool)
    result = await cspm_sync.sync_findings(
        pool, _export(_f("W1", "x", resource={"resource_id": "arn:aws:s3:::ghost"})),
        organization_id=org)
    assert result.findings_created == 1
    assert result.affects_edges == 0
    assert result.unresolved_assets == ["W1"]
    assert any("ghost" in w for w in result.warnings)


# ── lifecycle ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_idempotent(pool):
    org = await _org(pool)
    await _system(pool, org, cloud_resource_id=_ARN)
    exp = _export(_f("W1", "x", resource={"resource_id": _ARN}))
    await cspm_sync.sync_findings(pool, exp, organization_id=org)
    again = await cspm_sync.sync_findings(pool, exp, organization_id=org)
    assert again.findings_created == 0
    assert again.findings_updated == 1


@pytest.mark.asyncio
async def test_missing_finding_archived_as_remediated(pool):
    org = await _org(pool)
    await _system(pool, org, cloud_resource_id=_ARN)
    await cspm_sync.sync_findings(
        pool,
        _export(
            _f("W1", "x", resource={"resource_id": _ARN}),
            _f("W2", "y", resource={"resource_id": _ARN}),
            source="cspm:wiz",
        ),
        organization_id=org)
    # W1 fixed upstream — drops out of an otherwise populated export.
    result = await cspm_sync.sync_findings(
        pool,
        _export(_f("W2", "y", resource={"resource_id": _ARN}), source="cspm:wiz"),
        organization_id=org)
    assert result.resolved == ["finding:W1"]
    assert result.findings_resolved == 1
    fnd = await pool.fetchrow(
        "SELECT id, metadata, status FROM assets "
        "WHERE source = $1 AND organization_id = $2 AND source_ref = $3",
        "cspm:wiz", org, "finding:W1")
    assert fnd["status"] == "archived"
    assert _meta(fnd)["finding_status"] == "remediated"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    await _system(pool, org, cloud_resource_id=_ARN)
    result = await cspm_sync.sync_findings(
        pool, _export(_f("W1", "x", resource={"resource_id": _ARN})),
        organization_id=org, dry_run=True)
    assert result.findings_created == 1
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'cspm'") == 0


@pytest.mark.asyncio
async def test_invalid_export_rejected(pool):
    org = await _org(pool)
    result = await cspm_sync.sync_findings(pool, {"findings": [{"title": "no id"}]}, organization_id=org)
    assert result.errors


# ── interlock: CSPM findings feed risk aggregation (I4) ──────────────────


@pytest.mark.asyncio
async def test_cspm_findings_roll_into_risk_aggregation(pool):
    from grcen.services import risk_aggregation
    org = await _org(pool)
    await _system(pool, org, name="Prod Bucket", cloud_resource_id=_ARN)
    await cspm_sync.sync_findings(
        pool, _export(_f("W1", "S3 public", severity="high", resource={"resource_id": _ARN})),
        organization_id=org)
    agg = await risk_aggregation.aggregate_vuln_risks(pool, organization_id=org)
    assert agg.risks_created == 1   # the misconfig became a tracked Risk
    risk = await pool.fetchrow(
        "SELECT name, metadata FROM assets WHERE source = 'vuln-agg' AND type = 'risk'")
    assert "Prod Bucket" in risk["name"]
    assert _meta(risk)["severity"] == "high"


# ── HTTP ─────────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(pool, f"cspm_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "cspm", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_requires_sync_permission(pool):
    async with _client() as c:
        assert (await c.post("/api/sync/cspm", json=_export(_f("W1", "x")))).status_code == 401


@pytest.mark.asyncio
async def test_http_sync(pool):
    org = await _org(pool)
    await _system(pool, org, cloud_resource_id=_ARN)
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.post(
            "/api/sync/cspm",
            json=_export(_f("W1", "S3 public", resource={"resource_id": _ARN})),
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200
        assert r.json()["findings_created"] == 1
        assert r.json()["affects_edges"] == 1


# --- an empty export must not close the whole backlog ----------------------


@pytest.mark.asyncio
async def test_empty_export_is_refused(pool):
    """Same failure mode as the vuln connector: see test_vuln_sync."""
    org = await _org(pool)
    await cspm_sync.sync_findings(
        pool, _export(_f("c1", "Public bucket")), organization_id=org
    )

    result = await cspm_sync.sync_findings(pool, _export(), organization_id=org)

    assert result.errors, "empty export was accepted"
    assert "no findings" in result.errors[0]
    assert result.findings_resolved == 0


@pytest.mark.asyncio
async def test_empty_export_allowed_when_explicit(pool):
    org = await _org(pool)
    await cspm_sync.sync_findings(
        pool, _export(_f("c1", "Public bucket")), organization_id=org
    )

    result = await cspm_sync.sync_findings(
        pool, _export(), organization_id=org, allow_empty=True
    )

    assert not result.errors
    assert result.findings_resolved == 1
