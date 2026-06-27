"""AI control-to-requirement mapping suggester: proposals → draft approvals."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from grcen.models.asset import AssetType
from grcen.services import (
    ai_mapping_service,
    asset as asset_svc,
    catalog_sync,
    framework_service,
    organization_service,
)


async def _org(pool):
    return await organization_service.get_default_org_id(pool)


def _fake_client(mappings, stop_reason="tool_use"):
    """Stand-in for anthropic.AsyncAnthropic — returns a forced tool_use block."""
    block = SimpleNamespace(type="tool_use", name="propose_mappings",
                            input={"mappings": mappings})

    class _Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(stop_reason=stop_reason, content=[block])

    return SimpleNamespace(messages=_Messages())


async def _gap_framework(pool, org):
    """Framework with one gap requirement R1 + an unlinked candidate control."""
    await catalog_sync.sync_catalog(pool, {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwa", "name": "FW A",
                        "requirements": [{"ref": "fwa:R1", "name": "Require MFA"}]}],
        "controls": [],
    }, organization_id=org)
    fw = (await framework_service.list_frameworks(pool, organization_id=org))[0]
    detail = await framework_service.get_framework_detail(pool, fw.id, organization_id=org)
    req = detail.applicable_requirements[0]
    control = await asset_svc.create_asset(
        pool, organization_id=org, type=AssetType.CONTROL, name="MFA Enforcement")
    return fw.id, str(req.id), str(control.id)


async def _pending_rel_creates(pool, org):
    return await pool.fetch(
        """SELECT target_asset_id, payload FROM pending_changes
           WHERE organization_id = $1 AND action = 'relationship_create'
             AND status = 'pending'""",
        org,
    )


@pytest.mark.asyncio
async def test_high_confidence_queues_draft(pool):
    org = await _org(pool)
    fw_id, req_id, ctrl_id = await _gap_framework(pool, org)
    client = _fake_client([{"requirement_id": req_id, "control_id": ctrl_id,
                            "confidence": "high", "rationale": "Enforces MFA."}])
    result = await ai_mapping_service.suggest_mappings(
        pool, framework_id=fw_id, organization_id=org, user=_admin(pool), client=client)
    assert result["created"] == 1
    rows = await _pending_rel_creates(pool, org)
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["source_asset_id"] == ctrl_id
    assert payload["target_asset_id"] == req_id
    assert payload["relationship_type"] == "satisfies"
    assert payload["description"].startswith("AI-suggested (high)")


@pytest.mark.asyncio
async def test_low_confidence_not_queued(pool):
    org = await _org(pool)
    fw_id, req_id, ctrl_id = await _gap_framework(pool, org)
    client = _fake_client([{"requirement_id": req_id, "control_id": ctrl_id,
                            "confidence": "low", "rationale": "Tangential."}])
    result = await ai_mapping_service.suggest_mappings(
        pool, framework_id=fw_id, organization_id=org, user=_admin(pool), client=client)
    assert result["created"] == 0
    assert result["low_confidence"] == 1
    assert len(await _pending_rel_creates(pool, org)) == 0


@pytest.mark.asyncio
async def test_hallucinated_id_skipped(pool):
    org = await _org(pool)
    fw_id, req_id, ctrl_id = await _gap_framework(pool, org)
    client = _fake_client([{"requirement_id": str(uuid4()), "control_id": ctrl_id,
                            "confidence": "high", "rationale": "Off-list id."}])
    result = await ai_mapping_service.suggest_mappings(
        pool, framework_id=fw_id, organization_id=org, user=_admin(pool), client=client)
    assert result["created"] == 0
    assert result["skipped"] == 1


@pytest.mark.asyncio
async def test_no_controls_short_circuits(pool):
    org = await _org(pool)
    await catalog_sync.sync_catalog(pool, {
        "catalog_version": "1", "source": "autocomply",
        "frameworks": [{"ref": "fwb", "name": "FW B",
                        "requirements": [{"ref": "fwb:R1", "name": "R1"}]}],
        "controls": [],
    }, organization_id=org)
    fw = (await framework_service.list_frameworks(pool, organization_id=org))[0]
    # No client passed — must short-circuit before any API call.
    result = await ai_mapping_service.suggest_mappings(
        pool, framework_id=fw.id, organization_id=org, user=_admin(pool))
    assert result["created"] == 0
    assert result["reason"] == "no controls"


@pytest.mark.asyncio
async def test_route_unconfigured_flashes(auth_client):
    resp = await auth_client.post(
        f"/frameworks/{uuid4()}/ai-suggest-mappings", follow_redirects=False)
    assert resp.status_code == 302
    assert "not%20configured" in resp.headers["location"]


@pytest.mark.asyncio
async def test_route_configured_success(auth_client, monkeypatch):
    monkeypatch.setattr(ai_mapping_service, "is_configured", lambda: True)

    async def _stub(pool, *, framework_id, organization_id, user, client=None):
        return {"created": 2, "skipped": 1, "low_confidence": 0, "queued": []}

    monkeypatch.setattr(ai_mapping_service, "suggest_mappings", _stub)
    resp = await auth_client.post(
        f"/frameworks/{uuid4()}/ai-suggest-mappings", follow_redirects=False)
    assert resp.status_code == 302
    assert "2%20mapping" in resp.headers["location"]


# --- helper: a User for the service calls ---------------------------------
_ADMIN = {}


def _admin(pool):
    return _ADMIN["user"]


@pytest.fixture(autouse=True)
async def _seed_admin(pool):
    from grcen.models.user import UserRole
    from grcen.services.auth import create_user
    _ADMIN["user"] = await create_user(
        pool, f"aiadmin_{uuid4().hex[:8]}", "testpass", role=UserRole.ADMIN)
    yield
