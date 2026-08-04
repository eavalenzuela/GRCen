"""Tests for field-level redaction of sensitive custom fields."""

import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import Permission, UserRole, has_permission
from grcen.services import asset as asset_svc
from grcen.services import export_service, pdf_service, redaction
from grcen.services.auth import create_user


async def _person(pool, admin_id, email="alice@test", phone="555-0100", clearance="secret"):
    return await asset_svc.create_asset(
        pool,
        type=AssetType.PERSON,
        name="Alice",
        status="active",
        updated_by=admin_id,
        metadata_={
            "email": email,
            "phone": phone,
            "clearance_level": clearance,
            "title": "Engineer",
            "department": "Security",
        },
    )


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


# ── pure redaction logic ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redaction_masks_sensitive_fields_for_viewer(pool):
    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    meta = {"email": "a@b", "phone": "555", "title": "CEO"}
    out = redaction.redact_metadata(meta, AssetType.PERSON, viewer)
    assert out["email"] == redaction.REDACTED_PLACEHOLDER
    assert out["phone"] == redaction.REDACTED_PLACEHOLDER
    assert out["title"] == "CEO"  # not sensitive
    # Original unchanged
    assert meta["email"] == "a@b"


@pytest.mark.asyncio
async def test_redaction_noop_for_admin_and_auditor(pool):
    admin = await create_user(pool, f"a_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN)
    auditor = await create_user(pool, f"au_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.AUDITOR)
    meta = {"email": "a@b", "phone": "555"}
    for u in (admin, auditor):
        out = redaction.redact_metadata(meta, AssetType.PERSON, u)
        assert out == meta


@pytest.mark.asyncio
async def test_redaction_noop_for_editor(pool):
    editor = await create_user(pool, f"e_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.EDITOR)
    meta = {"email": "a@b"}
    out = redaction.redact_metadata(meta, AssetType.PERSON, editor)
    assert out["email"] == "a@b"


@pytest.mark.asyncio
async def test_redaction_skips_types_with_no_sensitive_fields(pool):
    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    # Policy has no sensitive fields
    meta = {"version": "1.0", "classification": "internal"}
    out = redaction.redact_metadata(meta, AssetType.POLICY, viewer)
    assert out == meta


@pytest.mark.asyncio
async def test_redaction_skips_empty_values(pool):
    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    meta = {"email": "", "phone": None, "clearance_level": "secret"}
    out = redaction.redact_metadata(meta, AssetType.PERSON, viewer)
    assert out["email"] == ""
    assert out["phone"] is None
    assert out["clearance_level"] == redaction.REDACTED_PLACEHOLDER


# ── permission wiring ────────────────────────────────────────────────────


def test_viewer_lacks_view_pii():
    assert not has_permission(UserRole.VIEWER, Permission.VIEW_PII)


def test_admin_editor_auditor_have_view_pii():
    for role in (UserRole.ADMIN, UserRole.EDITOR, UserRole.AUDITOR):
        assert has_permission(role, Permission.VIEW_PII)


# ── API redaction ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asset_detail_api_redacts_for_viewer(viewer_client, pool, admin_user):
    p = await _person(pool, admin_user.id)
    resp = await viewer_client.get(f"/api/assets/{p.id}")
    assert resp.status_code == 200
    meta = resp.json()["metadata_"]
    assert meta["email"] == redaction.REDACTED_PLACEHOLDER
    assert meta["phone"] == redaction.REDACTED_PLACEHOLDER
    assert meta["clearance_level"] == redaction.REDACTED_PLACEHOLDER
    assert meta["title"] == "Engineer"


@pytest.mark.asyncio
async def test_asset_detail_api_unredacted_for_admin(auth_client, pool, admin_user):
    p = await _person(pool, admin_user.id)
    resp = await auth_client.get(f"/api/assets/{p.id}")
    meta = resp.json()["metadata_"]
    assert meta["email"] == "alice@test"
    assert meta["phone"] == "555-0100"


@pytest.mark.asyncio
async def test_asset_list_api_redacts_for_viewer(viewer_client, pool, admin_user):
    await _person(pool, admin_user.id)
    resp = await viewer_client.get("/api/assets/?type=person")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["metadata_"]["email"] == redaction.REDACTED_PLACEHOLDER


@pytest.mark.asyncio
async def test_search_api_redacts_for_viewer(viewer_client, pool, admin_user):
    await _person(pool, admin_user.id)
    resp = await viewer_client.get("/api/assets/search?q=Alice")
    rows = resp.json()
    assert rows[0]["metadata_"]["email"] == redaction.REDACTED_PLACEHOLDER


# ── HTML page redaction ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asset_detail_page_redacts_for_viewer(viewer_client, pool, admin_user):
    p = await _person(pool, admin_user.id)
    resp = await viewer_client.get(f"/assets/{p.id}")
    assert resp.status_code == 200
    assert "alice@test" not in resp.text
    assert "555-0100" not in resp.text
    assert redaction.REDACTED_PLACEHOLDER in resp.text


@pytest.mark.asyncio
async def test_asset_detail_page_plaintext_for_admin(auth_client, pool, admin_user):
    p = await _person(pool, admin_user.id)
    resp = await auth_client.get(f"/assets/{p.id}")
    assert "alice@test" in resp.text


# ── Exports ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_csv_export_redacts_for_viewer(pool, admin_user):
    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    await _person(pool, admin_user.id)
    csv_out = await export_service.export_assets(
        pool, format="csv", asset_types=[AssetType.PERSON], user=viewer
    )
    assert "alice@test" not in csv_out
    assert "555-0100" not in csv_out
    assert redaction.REDACTED_PLACEHOLDER in csv_out


@pytest.mark.asyncio
async def test_json_export_plaintext_for_admin(pool, admin_user):
    await _person(pool, admin_user.id)
    out = await export_service.export_assets(
        pool, format="json", asset_types=[AssetType.PERSON], user=admin_user
    )
    assert "alice@test" in out


@pytest.mark.asyncio
async def test_export_without_user_redacts_by_default(pool, admin_user):
    """No user = most restrictive. Callers must pass ``user`` to get plaintext."""
    await _person(pool, admin_user.id)
    out = await export_service.export_assets(
        pool, format="json", asset_types=[AssetType.PERSON]
    )
    assert "alice@test" not in out
    assert redaction.REDACTED_PLACEHOLDER in out


# ── PDF redaction ────────────────────────────────────────────────────────
# Note: PDF streams are FlateDecode-compressed, so asserting on the raw bytes
# is unreliable either direction. We verify the redaction helper is invoked
# by spying on render-time metadata instead.


@pytest.mark.asyncio
async def test_pdf_renders_for_viewer_and_admin(pool, admin_user):
    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    p = await _person(pool, admin_user.id)

    admin_pdf = await pdf_service.render_asset_report(pool, p.id, user=admin_user)
    viewer_pdf = await pdf_service.render_asset_report(pool, p.id, user=viewer)
    assert admin_pdf is not None and admin_pdf.startswith(b"%PDF-")
    assert viewer_pdf is not None and viewer_pdf.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_pdf_service_passes_user_to_redaction(pool, admin_user, monkeypatch):
    """End-to-end wiring check: redact_metadata is called with the user."""
    captured = {}
    original = redaction.redact_metadata

    def spy(metadata, asset_type, user, **kwargs):
        captured["user"] = user
        captured["asset_type"] = asset_type
        return original(metadata, asset_type, user, **kwargs)

    monkeypatch.setattr(pdf_service.redaction, "redact_metadata", spy)

    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    p = await _person(pool, admin_user.id)
    await pdf_service.render_asset_report(pool, p.id, user=viewer)
    assert captured["user"] == viewer
    assert captured["asset_type"] == AssetType.PERSON


# ── token scope must gate PII, not just role ─────────────────────────────


def _admin_with_token_scope(admin, perms):
    """Copy of an admin carrying an API token's declared permission scope."""
    import dataclasses
    return dataclasses.replace(admin, token_permissions=perms)


@pytest.mark.asyncio
async def test_token_without_view_pii_gets_redacted(pool, admin_user):
    """A least-privilege token must not inherit its owner's PII access.

    require_permission already enforces role AND token scope; can_view_pii
    consulted the role alone, so a token minted without view_pii still
    received unmasked sensitive fields.
    """
    meta = {"email": "a@b", "phone": "555", "title": "CEO"}
    scoped = _admin_with_token_scope(admin_user, ["view"])

    assert redaction.can_view_pii(scoped) is False
    out = redaction.redact_metadata(meta, AssetType.PERSON, scoped)
    assert out["email"] == redaction.REDACTED_PLACEHOLDER
    assert out["phone"] == redaction.REDACTED_PLACEHOLDER
    assert out["title"] == "CEO"


@pytest.mark.asyncio
async def test_token_with_view_pii_sees_plaintext(pool, admin_user):
    meta = {"email": "a@b", "phone": "555"}
    scoped = _admin_with_token_scope(admin_user, ["view", "view_pii"])

    assert redaction.can_view_pii(scoped) is True
    assert redaction.redact_metadata(meta, AssetType.PERSON, scoped) == meta


@pytest.mark.asyncio
async def test_session_user_unaffected_by_token_scope(pool, admin_user):
    """token_permissions is None for session callers — role alone decides."""
    assert admin_user.token_permissions is None
    assert redaction.can_view_pii(admin_user) is True


@pytest.mark.asyncio
async def test_api_token_without_view_pii_redacts_over_http(pool, admin_user):
    from httpx import ASGITransport, AsyncClient

    from grcen.main import app
    from grcen.services import token_service

    asset = await _person(pool, admin_user.id)
    _tok, raw = await token_service.create_token(
        pool, admin_user.id, "no-pii", ["view"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            f"/api/assets/{asset.id}",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        meta = resp.json()["metadata_"]
        assert meta["email"] == redaction.REDACTED_PLACEHOLDER
        assert meta["title"] == "Engineer"


# ── admin-configured overrides must apply on every egress path ───────────


@pytest.mark.asyncio
async def test_org_override_applies_on_rest_and_pdf(pool, admin_user):
    """A field promoted to sensitive by an admin must be masked everywhere.

    The REST and PDF paths used the code-only synchronous lookup, so
    override-promoted fields leaked there while the HTML page masked them.
    """
    from httpx import ASGITransport, AsyncClient

    from grcen.main import app
    from grcen.services import token_service

    asset = await _person(pool, admin_user.id)
    # Promote a normally-public field for this org.
    await pool.execute(
        """INSERT INTO sensitive_field_overrides
               (organization_id, asset_type, field_name, sensitive)
           VALUES ($1, $2, $3, true)""",
        admin_user.organization_id, AssetType.PERSON.value, "department",
    )

    viewer = await create_user(
        pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER,
        organization_id=admin_user.organization_id,
    )
    _tok, raw = await token_service.create_token(
        pool, viewer.id, "viewer-tok", ["view"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        headers = {"Authorization": f"Bearer {raw}"}

        detail = await c.get(f"/api/assets/{asset.id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["metadata_"]["department"] == redaction.REDACTED_PLACEHOLDER

        listing = await c.get("/api/assets/?types=person", headers=headers)
        assert listing.status_code == 200
        rows = [r for r in listing.json()["items"] if r["id"] == str(asset.id)]
        assert rows and rows[0]["metadata_"]["department"] == redaction.REDACTED_PLACEHOLDER

    pdf = await pdf_service.render_asset_report(
        pool, asset.id, user=viewer, organization_id=viewer.organization_id
    )
    assert pdf[:4] == b"%PDF"
    assert b"Security" not in pdf
