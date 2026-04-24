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

    def spy(metadata, asset_type, user):
        captured["user"] = user
        captured["asset_type"] = asset_type
        return original(metadata, asset_type, user)

    monkeypatch.setattr(pdf_service.redaction, "redact_metadata", spy)

    viewer = await create_user(pool, f"v_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.VIEWER)
    p = await _person(pool, admin_user.id)
    await pdf_service.render_asset_report(pool, p.id, user=viewer)
    assert captured["user"] == viewer
    assert captured["asset_type"] == AssetType.PERSON
