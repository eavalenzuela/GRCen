"""Tests for the auditor evidence package (roadmap E2)."""
import io
import json
import uuid
import zipfile
from datetime import date

import pytest

from grcen.models.attachment import AttachmentKind
from grcen.services import (
    attachment as attachment_service,
    catalog_sync,
    control_test_service,
    evidence_package,
    organization_service,
)


_CATALOG = {
    "frameworks": [{
        "ref": "soc2", "name": "SOC 2",
        "requirements": [
            {"ref": "soc2:CC6.1", "name": "CC6.1 Logical access"},
            {"ref": "soc2:CC7.2", "name": "CC7.2 Monitoring"},
        ],
    }],
    "controls": [
        {"ref": "01.a", "name": "Access Control Policy", "satisfies": ["soc2:CC6.1"]},
    ],
}


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


async def _seed(pool, org):
    await catalog_sync.sync_catalog(pool, _CATALOG, organization_id=org)
    fw_id = await pool.fetchval(
        "SELECT id FROM assets WHERE source_ref = 'framework:soc2' AND organization_id = $1", org)
    ctrl_id = await pool.fetchval(
        "SELECT id FROM assets WHERE source_ref = 'control:01.a' AND organization_id = $1", org)
    return fw_id, ctrl_id


def _zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


# ── service-level ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_package_contains_all_sections(pool, tmp_path):
    org = await _org(pool)
    fw_id, ctrl_id = await _seed(pool, org)

    # a recorded test run
    await control_test_service.record_test_run(
        pool, ctrl_id, result="pass", method="manual", organization_id=org)
    # a real evidence file on the control
    fp = tmp_path / "screenshot.txt"
    fp.write_text("PASS screenshot")
    await attachment_service.create_attachment(
        pool, asset_id=ctrl_id, kind=AttachmentKind.FILE,
        name="screenshot.txt", url_or_path=str(fp), organization_id=org)
    # a URL evidence link
    await attachment_service.create_attachment(
        pool, asset_id=ctrl_id, kind=AttachmentKind.URL,
        name="Policy doc", url_or_path="https://example.com/policy", organization_id=org)
    # an audit-trail event on the control
    await pool.execute(
        """INSERT INTO audit_log
               (username, action, entity_type, entity_id, entity_name, changes, organization_id)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)""",
        "tester", "update", "asset", ctrl_id, "Access Control Policy",
        '{"effectiveness": ["not_tested", "effective"]}', org,
    )

    result = await evidence_package.build_package(
        pool, organization_id=org, framework_id=fw_id)
    assert result is not None
    assert result.framework_name == "SOC 2"
    assert result.filename.startswith("evidence-package-SOC-2-")
    assert result.filename.endswith(".zip")

    zf = _zip(result.content)
    names = zf.namelist()
    for expected in [
        "manifest.json", "README.txt", "statement_of_applicability.csv",
        "control_library.csv", "control_test_ledger.csv", "audit_trail.csv",
        "evidence/_index.csv",
    ]:
        assert expected in names, f"missing {expected}"

    soa = zf.read("statement_of_applicability.csv").decode()
    assert "CC6.1" in soa and "CC7.2" in soa

    lib = zf.read("control_library.csv").decode()
    assert "Access Control Policy" in lib

    ledger = zf.read("control_test_ledger.csv").decode()
    assert "pass" in ledger

    audit = zf.read("audit_trail.csv").decode()
    assert "effectiveness" in audit

    # evidence file bundled and (undecrypted here) content intact
    ev_files = [n for n in names if n.startswith("evidence/") and n.endswith("screenshot.txt")]
    assert ev_files
    assert zf.read(ev_files[0]).decode() == "PASS screenshot"

    idx = zf.read("evidence/_index.csv").decode()
    assert "Policy doc" in idx and "https://example.com/policy" in idx

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["framework"]["name"] == "SOC 2"
    assert manifest["counts"]["requirements"] == 2
    assert manifest["counts"]["controls"] == 1
    assert manifest["counts"]["test_runs"] == 1
    assert manifest["counts"]["evidence_files"] == 1
    assert manifest["counts"]["evidence_links"] == 1
    assert manifest["counts"]["audit_events"] == 1


@pytest.mark.asyncio
async def test_missing_framework_returns_none(pool):
    org = await _org(pool)
    result = await evidence_package.build_package(
        pool, organization_id=org, framework_id=uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_framework_with_no_controls(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(
        pool,
        {"frameworks": [{"ref": "iso", "name": "ISO 27001",
                         "requirements": [{"ref": "iso:A.1", "name": "A.1"}]}],
         "controls": []},
        organization_id=org,
    )
    fw_id = await pool.fetchval(
        "SELECT id FROM assets WHERE source_ref = 'framework:iso'")
    result = await evidence_package.build_package(
        pool, organization_id=org, framework_id=fw_id)
    assert result is not None
    assert result.counts["controls"] == 0
    assert result.counts["test_runs"] == 0
    # still a valid, complete package
    assert "statement_of_applicability.csv" in _zip(result.content).namelist()


@pytest.mark.asyncio
async def test_period_filter_scopes_ledger(pool):
    org = await _org(pool)
    fw_id, ctrl_id = await _seed(pool, org)
    await control_test_service.record_test_run(
        pool, ctrl_id, result="pass", method="manual", organization_id=org)

    # A period entirely in the future excludes today's run.
    result = await evidence_package.build_package(
        pool, organization_id=org, framework_id=fw_id, since=date(2999, 1, 1))
    ledger = _zip(result.content).read("control_test_ledger.csv").decode()
    assert "pass" not in ledger  # header only, the run is outside the window
    assert result.counts["test_runs"] == 0


@pytest.mark.asyncio
async def test_encrypted_attachment_decrypted_into_package(pool, tmp_path, monkeypatch):
    """An encrypted evidence file is decrypted into the ZIP."""
    org = await _org(pool)
    fw_id, ctrl_id = await _seed(pool, org)

    captured = {}

    def fake_decrypt(data, scope):
        captured["scope"] = scope
        return b"decrypted evidence"

    monkeypatch.setattr(evidence_package, "decrypt_bytes", fake_decrypt)
    fp = tmp_path / "enc.bin"
    fp.write_bytes(b"ciphertext")
    await attachment_service.create_attachment(
        pool, asset_id=ctrl_id, kind=AttachmentKind.FILE, name="enc.bin",
        url_or_path=str(fp), encrypted=True, organization_id=org)

    result = await evidence_package.build_package(
        pool, organization_id=org, framework_id=fw_id)
    zf = _zip(result.content)
    ev = [n for n in zf.namelist() if n.endswith("enc.bin")]
    assert ev
    assert zf.read(ev[0]) == b"decrypted evidence"
    assert captured["scope"] == "file_contents"


# ── HTTP route ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_download_zip(auth_client, pool):
    org = await _org(pool)
    fw_id, _ = await _seed(pool, org)
    resp = await auth_client.get(f"/frameworks/{fw_id}/evidence-package.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "evidence-package" in resp.headers["content-disposition"]
    assert "manifest.json" in _zip(resp.content).namelist()
    # export is access-logged
    assert await pool.fetchval(
        "SELECT count(*) FROM data_access_log WHERE action = 'export' "
        "AND entity_name LIKE 'evidence-package-%'"
    ) == 1


@pytest.mark.asyncio
async def test_http_404_for_missing_framework(auth_client):
    resp = await auth_client.get(f"/frameworks/{uuid.uuid4()}/evidence-package.zip")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_auditor_can_download(auditor_client, pool):
    org = await _org(pool)
    fw_id, _ = await _seed(pool, org)
    resp = await auditor_client.get(f"/frameworks/{fw_id}/evidence-package.zip")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_download(viewer_client, pool):
    org = await _org(pool)
    fw_id, _ = await _seed(pool, org)
    resp = await viewer_client.get(f"/frameworks/{fw_id}/evidence-package.zip")
    assert resp.status_code in (302, 403)
