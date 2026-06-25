"""Register framework — decision #3: real `incident_status` lifecycle.

The incident register now leads with an `incident_status` enum (open →
investigating → contained → resolved → closed) instead of the Slice-1 stop-gap
computed open/closed signal. Covers the field, the curated column/lifecycle, bulk
transitions, and the status-based "Open" metric.
"""
import pytest

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import AssetType
from grcen import registers
from grcen.services import asset as asset_svc
from grcen.services import register_service


async def _incident(pool, name, **meta):
    return await asset_svc.create_asset(pool, type=AssetType.INCIDENT, name=name, metadata_=meta or {})


def test_incident_status_field_and_lifecycle_config():
    fields = {f.name: f for f in CUSTOM_FIELDS[AssetType.INCIDENT]}
    assert "incident_status" in fields
    assert fields["incident_status"].field_type == "enum"
    assert fields["incident_status"].choices == [
        "open", "triaged", "investigating", "contained", "resolved", "closed", "reopened"
    ]
    reg = registers.REGISTERS[AssetType.INCIDENT]
    assert reg.lifecycle_column == "meta.incident_status"
    assert "meta.incident_status" in reg.bulk_fields


@pytest.mark.asyncio
async def test_incident_register_shows_status_column(auth_client, pool):
    await _incident(pool, "Phish-001", incident_status="investigating", severity="high")
    page = await auth_client.get("/assets?type=incident&columns=curated")
    assert page.status_code == 200
    assert "Status" in page.text            # curated lead column header
    assert "Investigating" in page.text     # enum value, title-cased


@pytest.mark.asyncio
async def test_incident_status_bulk_transition(editor_client, pool):
    a = await _incident(pool, "Breach-001", incident_status="open")
    resp = await editor_client.post(
        "/assets/bulk-update?type=incident",
        data={"asset_ids": [str(a.id)], "meta.incident_status": "closed"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    import json
    meta = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", a.id)
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["incident_status"] == "closed"


@pytest.mark.asyncio
async def test_incident_status_bulk_rejects_invalid(editor_client, pool):
    a = await _incident(pool, "Breach-002", incident_status="open")
    await editor_client.post(
        "/assets/bulk-update?type=incident",
        data={"asset_ids": [str(a.id)], "meta.incident_status": "totally-bogus"},
        follow_redirects=False,
    )
    import json
    meta = await pool.fetchval("SELECT metadata FROM assets WHERE id = $1", a.id)
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["incident_status"] == "open"  # unchanged


@pytest.mark.asyncio
async def test_open_metric_is_status_based_with_legacy_fallback(pool):
    await _incident(pool, "I-new")                                    # no status, no resolved_at → open
    await _incident(pool, "I-inv", incident_status="investigating")   # open
    await _incident(pool, "I-reop", incident_status="reopened")       # open (not terminal)
    await _incident(pool, "I-res", incident_status="resolved")        # terminal → not open
    await _incident(pool, "I-cls", incident_status="closed")          # terminal → not open
    # Legacy incident: predates the field (no incident_status) but is genuinely
    # resolved (resolved_at set) → must NOT be miscounted as open (the A1 fix).
    await _incident(pool, "I-legacy", resolved_at="2025-01-01T00:00:00Z")

    reg = registers.REGISTERS[AssetType.INCIDENT]
    metrics = await register_service.build_metrics(pool, reg, organization_id=None)
    open_card = next(m for m in metrics if m["label"] == "Open")
    assert open_card["value"] == 3  # I-new, I-inv, I-reop (legacy-resolved excluded)
