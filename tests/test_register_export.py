"""Register framework — Slice 3 (export-from-view + numeric sort).

Covers numeric custom-field sorting, export-from-view filter/sort parity, and the
override-aware redaction fix on the export path (the old export used code-only
redaction and leaked org/per-asset promoted fields).
"""
import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import export_service, redaction
from grcen.services.auth import create_user


async def _default_org_id(pool):
    return await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")


async def _vendor(pool, name, **meta):
    return await asset_svc.create_asset(pool, type=AssetType.VENDOR, name=name, metadata_=meta or {})


# ── numeric meta sort ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_numeric_meta_sort(pool):
    await asset_svc.create_asset(pool, type=AssetType.ORGANIZATIONAL_UNIT, name="OU-2", metadata_={"headcount": 2})
    await asset_svc.create_asset(pool, type=AssetType.ORGANIZATIONAL_UNIT, name="OU-10", metadata_={"headcount": 10})
    await asset_svc.create_asset(pool, type=AssetType.ORGANIZATIONAL_UNIT, name="OU-3", metadata_={"headcount": 3})
    await asset_svc.create_asset(pool, type=AssetType.ORGANIZATIONAL_UNIT, name="OU-none")  # no headcount

    items, _ = await asset_svc.list_assets(
        pool, asset_type=AssetType.ORGANIZATIONAL_UNIT, sort="meta.headcount", order="asc"
    )
    names = [a.name for a in items]
    # Numeric, not lexical: 2 < 3 < 10 (lexical would put "10" before "2").
    assert names.index("OU-2") < names.index("OU-3") < names.index("OU-10")
    assert names[-1] == "OU-none"  # NULLS LAST, and a missing value can't abort the sort


# ── export-from-view parity ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_from_view_respects_filters(auth_client, pool):
    await _vendor(pool, "Acme", tier="critical")
    await _vendor(pool, "Globex", tier="critical")
    await _vendor(pool, "Initech", tier="low")
    resp = await auth_client.get("/assets/export?type=vendor&meta_key=tier&meta_value=critical&format=csv")
    assert resp.status_code == 200
    assert "Acme" in resp.text and "Globex" in resp.text
    assert "Initech" not in resp.text  # filtered out


@pytest.mark.asyncio
async def test_export_from_view_respects_sort(auth_client, pool):
    for n in ("Alpha", "Bravo", "Charlie"):
        await _vendor(pool, n)
    resp = await auth_client.get("/assets/export?type=vendor&sort=name&order=desc&format=json")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert names == ["Charlie", "Bravo", "Alpha"]


@pytest.mark.asyncio
async def test_export_excludes_posture_types_by_default(auth_client, pool):
    await _vendor(pool, "Acme")
    await asset_svc.create_asset(pool, type=AssetType.ANSWER, name="Do you encrypt at rest?")
    resp = await auth_client.get("/assets/export?format=csv")
    assert "Acme" in resp.text
    assert "Do you encrypt at rest?" not in resp.text  # posture excluded, mirrors /assets


# ── export honors sensitivity overrides (the §9 fix) ────────────────────────


@pytest.mark.asyncio
async def test_export_masks_org_promoted_field_for_non_pii(pool):
    """Service-level: no HTTP role has EXPORT without VIEW_PII, so exercise the
    override-aware masking directly. Pre-fix the export used code-only redaction
    and would have leaked this."""
    await _vendor(pool, "Acme", security_contact="SECRET-CONTACT-XYZ")
    org_id = await _default_org_id(pool)
    await redaction.upsert_override(pool, org_id, AssetType.VENDOR, "security_contact", True)

    viewer = await create_user(pool, "exp_viewer", "pw", role=UserRole.VIEWER)
    admin = await create_user(pool, "exp_admin", "pw", role=UserRole.ADMIN)

    masked = await export_service.export_assets(
        pool, format="csv", asset_type=AssetType.VENDOR, user=viewer, organization_id=org_id
    )
    assert "SECRET-CONTACT-XYZ" not in masked
    assert redaction.REDACTED_PLACEHOLDER in masked

    unmasked = await export_service.export_assets(
        pool, format="csv", asset_type=AssetType.VENDOR, user=admin, organization_id=org_id
    )
    assert "SECRET-CONTACT-XYZ" in unmasked  # admin has VIEW_PII


@pytest.mark.asyncio
async def test_export_buttons_render_on_register(auth_client, pool):
    await _vendor(pool, "Acme")
    page = await auth_client.get("/assets?type=vendor")
    assert "/assets/export?" in page.text
    assert "format=csv" in page.text and "format=json" in page.text
