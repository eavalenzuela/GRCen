"""Tests for the vulnerability-findings sync (roadmap I4, uvt → GRCen)."""
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from grcen.main import app
from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import organization_service, vuln_sync
from grcen.services.auth import create_user
from grcen.services.token_service import create_token


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _export(*findings, source=None):
    e = {"findings": list(findings)}
    if source is not None:
        e["source"] = source
    return e


def _finding(fid, title, **over):
    f = {"finding_id": fid, "title": title}
    f.update(over)
    return f


async def _finding_row(pool, org, ref):
    return await pool.fetchrow(
        "SELECT id, name, status, metadata FROM assets "
        "WHERE organization_id = $1 AND source = 'uvt' AND source_ref = $2",
        org, ref,
    )


def _meta(row):
    m = row["metadata"]
    return json.loads(m) if isinstance(m, str) else m


# ── service-level ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creates_findings_and_affects_edge(pool):
    org = await _org(pool)
    # A System the finding affects, matched by hostname.
    system = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Prod Web",
        metadata_={"hostname": "web01"}, organization_id=org,
    )
    result = await vuln_sync.sync_findings(
        pool,
        _export(_finding(
            "F1", "OpenSSL RCE", severity="critical", status="open",
            cve_id="CVE-2024-1234", cvss_score="9.8", scanner="qualys",
            affects={"hostname": "web01"},
        )),
        organization_id=org,
    )
    assert not result.errors
    assert result.findings_created == 1
    assert result.affects_edges == 1
    assert result.unresolved_assets == []

    fnd = await _finding_row(pool, org, "finding:F1")
    meta = _meta(fnd)
    assert meta["cve_id"] == "CVE-2024-1234"
    assert meta["severity"] == "critical"
    assert meta["finding_status"] == "open"
    assert meta["source"] == "automated"
    assert meta["affected_asset"] == "web01"

    # System --affected_by--> Finding
    edge = await pool.fetchrow(
        "SELECT source_asset_id, target_asset_id, relationship_type FROM relationships "
        "WHERE source = 'uvt' AND source_ref = 'affects:F1'"
    )
    assert edge["relationship_type"] == "affected_by"
    assert edge["source_asset_id"] == system.id
    assert edge["target_asset_id"] == fnd["id"]


@pytest.mark.asyncio
async def test_unresolved_affects_warns_but_creates_finding(pool):
    org = await _org(pool)
    result = await vuln_sync.sync_findings(
        pool,
        _export(_finding("F2", "Dangling", affects={"hostname": "ghost-host"})),
        organization_id=org,
    )
    assert result.findings_created == 1
    assert result.affects_edges == 0
    assert result.unresolved_assets == ["F2"]
    assert any("ghost-host" in w for w in result.warnings)
    # the finding still exists
    assert await _finding_row(pool, org, "finding:F2") is not None


@pytest.mark.asyncio
async def test_resolve_by_external_id_priority(pool):
    org = await _org(pool)
    system = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="DB",
        metadata_={"external_id": "sys_abc", "ip_address": "10.0.0.9"},
        organization_id=org,
    )
    result = await vuln_sync.sync_findings(
        pool,
        _export(_finding("F3", "SQLi", affects={"external_id": "sys_abc", "ip_address": "10.0.0.9"})),
        organization_id=org,
    )
    assert result.affects_edges == 1
    edge = await pool.fetchrow(
        "SELECT source_asset_id FROM relationships WHERE source_ref = 'affects:F3'"
    )
    assert edge["source_asset_id"] == system.id


@pytest.mark.asyncio
async def test_resync_is_idempotent(pool):
    org = await _org(pool)
    exp = _export(_finding("F1", "A"), _finding("F2", "B"))
    await vuln_sync.sync_findings(pool, exp, organization_id=org)
    again = await vuln_sync.sync_findings(pool, exp, organization_id=org)
    assert again.findings_created == 0
    assert again.findings_updated == 2
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'uvt' AND organization_id = $1", org
    ) == 2


@pytest.mark.asyncio
async def test_missing_finding_is_resolved_and_archived(pool):
    org = await _org(pool)
    await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "A"), _finding("F2", "B")), organization_id=org
    )
    # F2 remediated upstream — drops out of the export.
    result = await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "A")), organization_id=org
    )
    assert result.resolved == ["finding:F2"]
    assert result.findings_resolved == 1
    f2 = await _finding_row(pool, org, "finding:F2")
    assert f2["status"] == "archived"
    assert _meta(f2)["finding_status"] == "remediated"


@pytest.mark.asyncio
async def test_keep_resolved_flag(pool):
    org = await _org(pool)
    await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "A"), _finding("F2", "B")), organization_id=org
    )
    result = await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "A")), organization_id=org, resolve_missing=False
    )
    assert result.resolved == ["finding:F2"]
    assert result.findings_resolved == 0
    f2 = await _finding_row(pool, org, "finding:F2")
    assert f2["status"] == "active"  # reported but untouched


@pytest.mark.asyncio
async def test_moved_finding_updates_affects_edge_in_place(pool):
    org = await _org(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Host A",
        metadata_={"hostname": "a"}, organization_id=org)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Host B",
        metadata_={"hostname": "b"}, organization_id=org)
    await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "V", affects={"hostname": "a"})), organization_id=org)
    # same finding now reported against host b — the edge is re-pointed, but the
    # source_ref (affects:F1) is stable so it updates in place, not prunes.
    moved = await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "V", affects={"hostname": "b"})), organization_id=org)
    assert moved.affects_edges == 1
    edge = await pool.fetchrow(
        "SELECT a.name FROM relationships r JOIN assets a ON a.id = r.source_asset_id "
        "WHERE r.source_ref = 'affects:F1'")
    assert edge["name"] == "Host B"


@pytest.mark.asyncio
async def test_dropped_affects_prunes_edge(pool):
    org = await _org(pool)
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Host A",
        metadata_={"hostname": "a"}, organization_id=org)
    await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "V", affects={"hostname": "a"})), organization_id=org)
    # finding still present but no longer tied to a host
    result = await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "V")), organization_id=org)
    assert result.edges_pruned == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM relationships WHERE source_ref = 'affects:F1'") == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    org = await _org(pool)
    result = await vuln_sync.sync_findings(
        pool, _export(_finding("F1", "A"), _finding("F2", "B")),
        organization_id=org, dry_run=True)
    assert result.dry_run
    assert result.findings_created == 2
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'uvt'") == 0


@pytest.mark.asyncio
async def test_invalid_export_rejected(pool):
    org = await _org(pool)
    result = await vuln_sync.sync_findings(
        pool, {"findings": [{"title": "no id"}]}, organization_id=org)
    assert result.errors
    assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'uvt'") == 0


@pytest.mark.asyncio
async def test_custom_source_tag(pool):
    org = await _org(pool)
    await vuln_sync.sync_findings(
        pool, _export(_finding("Q1", "Qualys finding"), source="qualys"),
        organization_id=org)
    assert await pool.fetchval(
        "SELECT count(*) FROM assets WHERE source = 'qualys' AND type = 'finding'") == 1


# ── HTTP endpoint ────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sync_token(pool) -> str:
    admin = await create_user(pool, f"vulnadmin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    _, raw = await create_token(pool, admin.id, "uvt", ["sync"], is_service_account=True)
    return raw


@pytest.mark.asyncio
async def test_http_requires_sync_permission(pool):
    async with _client() as c:
        r = await c.post("/api/sync/vulns", json=_export(_finding("F1", "A")))
        assert r.status_code == 401
        admin = await create_user(pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
        _, raw = await create_token(pool, admin.id, "t", ["view"], is_service_account=True)
        r = await c.post(
            "/api/sync/vulns", json=_export(_finding("F1", "A")),
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_http_sync_and_dry_run(pool):
    org = await _org(pool)
    raw = await _sync_token(pool)
    async with _client() as c:
        h = {"Authorization": f"Bearer {raw}"}
        dr = await c.post(
            "/api/sync/vulns", params={"dry_run": "true"},
            json=_export(_finding("F1", "A", cve_id="CVE-1")), headers=h)
        assert dr.status_code == 200
        assert dr.json()["findings_created"] == 1
        assert await pool.fetchval("SELECT count(*) FROM assets WHERE source = 'uvt'") == 0

        r = await c.post(
            "/api/sync/vulns", json=_export(_finding("F1", "A", cve_id="CVE-1")), headers=h)
        assert r.status_code == 200
        assert r.json()["findings_created"] == 1
        assert await pool.fetchval(
            "SELECT count(*) FROM assets WHERE source = 'uvt' AND organization_id = $1", org
        ) == 1


@pytest.mark.asyncio
async def test_http_invalid_export_400(pool):
    raw = await _sync_token(pool)
    async with _client() as c:
        r = await c.post(
            "/api/sync/vulns", json={"findings": [{"title": "no id"}]},
            headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 400
        assert r.json()["errors"]


# --- an empty export must not close the whole backlog ----------------------


@pytest.mark.asyncio
async def test_empty_export_is_refused(pool):
    """A scanner that fails auth returns {"findings": []}.

    Treating that as "everything is remediated" silently closes the entire
    open-vulnerability backlog — a false negative in a compliance tool.
    """
    org = await _org(pool)
    await vuln_sync.sync_findings(
        pool, _export(_finding("v1", "CVE-1"), _finding("v2", "CVE-2")),
        organization_id=org,
    )
    assert (await _finding_row(pool, org, "finding:v1"))["status"] == "active"

    result = await vuln_sync.sync_findings(pool, _export(), organization_id=org)

    assert result.errors, "empty export was accepted"
    assert "no findings" in result.errors[0]
    assert result.findings_resolved == 0
    assert (await _finding_row(pool, org, "finding:v1"))["status"] == "active"
    assert (await _finding_row(pool, org, "finding:v2"))["status"] == "active"


@pytest.mark.asyncio
async def test_empty_export_allowed_when_explicit(pool):
    org = await _org(pool)
    await vuln_sync.sync_findings(
        pool, _export(_finding("v1", "CVE-1")), organization_id=org
    )

    result = await vuln_sync.sync_findings(
        pool, _export(), organization_id=org, allow_empty=True
    )

    assert not result.errors
    assert result.findings_resolved == 1
    assert (await _finding_row(pool, org, "finding:v1"))["status"] == "archived"
