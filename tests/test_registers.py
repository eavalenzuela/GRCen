"""Register framework — Slice 1.

Covers: the /registers index, the /registers/{slug} alias (canonical + redirect +
404), curated vs. all columns, the metrics header, and — critically — that the
register/list path now honors per-org and per-asset sensitivity *overrides*
(closing the latent leak the framework's curated columns / exports would widen).
"""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc
from grcen.services import redaction


async def _default_org_id(pool):
    return await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")


async def _vendor(pool, name, **meta):
    return await asset_svc.create_asset(
        pool, type=AssetType.VENDOR, name=name, metadata_=meta or {},
    )


# ── index + alias ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registers_index_lists_registers_and_counts(auth_client, pool):
    await _vendor(pool, "Acme SaaS", tier="critical")
    await _vendor(pool, "Globex Hosting", tier="high")
    resp = await auth_client.get("/registers")
    assert resp.status_code == 200
    # Group headings + register names render.
    assert "Vendors" in resp.text
    assert "Incidents" in resp.text
    assert "Policies" in resp.text
    # Count badge reflects the two vendors just created.
    assert "2" in resp.text


@pytest.mark.asyncio
async def test_alias_redirects_to_canonical_list_with_curated_columns(auth_client):
    resp = await auth_client.get("/registers/vendors", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("/assets?type=vendor")
    assert "columns=curated" in loc
    assert "sort=meta.next_assessment_due" in loc


@pytest.mark.asyncio
async def test_alias_for_bespoke_type_redirects_to_dedicated_page(auth_client):
    resp = await auth_client.get("/registers/risks", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/risk-management"


@pytest.mark.asyncio
async def test_unknown_register_slug_404(auth_client):
    resp = await auth_client.get("/registers/does-not-exist", follow_redirects=False)
    assert resp.status_code == 404


# ── curated vs all columns ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_curated_columns_are_a_subset(auth_client, pool):
    await _vendor(pool, "Acme", tier="critical", vendor_type="saas")
    curated = await auth_client.get("/assets?type=vendor&columns=curated")
    assert curated.status_code == 200
    # Curated header uses the register's short labels…
    assert "Assessment" in curated.text
    assert "Contract End" in curated.text
    # …and omits non-curated fields like "Vendor Type".
    assert "Vendor Type" not in curated.text

    # Ad-hoc /assets?type=vendor preserves today's full-column behavior.
    full = await auth_client.get("/assets?type=vendor")
    assert "Vendor Type" in full.text


@pytest.mark.asyncio
async def test_metrics_header_renders_for_register(auth_client, pool):
    await _vendor(pool, "Acme", tier="critical", assessment_result="not_approved")
    page = await auth_client.get("/assets?type=vendor")
    assert "Overdue Assessments" in page.text
    assert "Critical Vendors" in page.text
    assert "Approval Gaps" in page.text


@pytest.mark.asyncio
async def test_h1_reflects_register(auth_client):
    page = await auth_client.get("/assets?type=incident")
    assert "Incidents" in page.text  # H1 + title use the register plural


# ── redaction prerequisite (the latent-leak regression) ─────────────────────


@pytest.mark.asyncio
async def test_list_drops_org_promoted_sensitive_column(viewer_client, auth_client, pool):
    """A per-ORG sensitivity override must remove a field from the list for
    everyone — previously the list only filtered *code-default* sensitive
    fields, so an org-promoted field leaked. (The list never surfaces sensitive
    fields as columns, even to VIEW_PII admins; per-user reveal is the detail
    page's job.)"""
    await _vendor(pool, "Acme", security_contact="SECRET-CONTACT-XYZ")

    # Before promotion: an ordinary non-sensitive field shows in the full list.
    pre = await auth_client.get("/assets?type=vendor")
    assert "SECRET-CONTACT-XYZ" in pre.text

    org_id = await _default_org_id(pool)
    await redaction.upsert_override(
        pool, org_id, AssetType.VENDOR, "security_contact", True
    )

    # After promotion: gone for the admin AND the viewer (the bug fix).
    post_admin = await auth_client.get("/assets?type=vendor")
    assert "SECRET-CONTACT-XYZ" not in post_admin.text
    post_viewer = await viewer_client.get("/assets?type=vendor")
    assert post_viewer.status_code == 200
    assert "SECRET-CONTACT-XYZ" not in post_viewer.text


@pytest.mark.asyncio
async def test_list_masks_per_asset_promoted_value(viewer_client, pool):
    """A per-ASSET override masks only that asset's cell; siblings still show."""
    secret = await _vendor(pool, "Acme", security_contact="ALPHA-SECRET")
    await _vendor(pool, "Globex", security_contact="BRAVO-PUBLIC")
    await redaction.upsert_asset_override(pool, secret.id, "security_contact", True)

    resp = await viewer_client.get("/assets?type=vendor")
    assert resp.status_code == 200
    assert "ALPHA-SECRET" not in resp.text
    assert redaction.REDACTED_PLACEHOLDER in resp.text
    assert "BRAVO-PUBLIC" in resp.text


# ── existing behavior preserved ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inventory_list_unaffected(auth_client, pool):
    """/assets (no type) shows no per-type meta columns and no metrics header."""
    await _vendor(pool, "Acme", tier="critical")
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    assert "sort=meta.tier" not in resp.text
    assert "Overdue Assessments" not in resp.text
