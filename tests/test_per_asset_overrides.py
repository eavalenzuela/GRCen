"""Per-asset redaction overrides layered on top of per-type rules."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import UserRole
from grcen.services import asset as asset_svc
from grcen.services import organization_service, redaction


@pytest.fixture
async def viewer():
    """A non-admin user without VIEW_PII so redaction kicks in."""
    return User(
        id=uuid.uuid4(),
        username="viewer",
        hashed_password="!unusable",
        is_active=True,
        role=UserRole.VIEWER,
        created_at=None, updated_at=None,
        organization_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_per_asset_override_promotes_field(pool, viewer):
    org_id = await organization_service.get_default_org_id(pool)
    a = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="One",
        metadata_={"environment": "prod", "url": "https://x"},
    )
    # Mark only this asset's environment field sensitive.
    await redaction.upsert_asset_override(pool, a.id, "environment", True)
    masked = await redaction.redact_metadata_async(
        pool, a.metadata_, AssetType.SYSTEM, viewer, org_id, asset_id=a.id,
    )
    assert masked["environment"] == "[redacted]"
    assert masked["url"] == "https://x"


@pytest.mark.asyncio
async def test_per_asset_override_can_declassify(pool, viewer):
    """A per-asset sensitive=False overrides a code-default sensitive field."""
    org_id = await organization_service.get_default_org_id(pool)
    p = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Alice",
        metadata_={"email": "alice@example.com", "phone": "555"},
    )
    await redaction.upsert_asset_override(pool, p.id, "email", False)
    masked = await redaction.redact_metadata_async(
        pool, p.metadata_, AssetType.PERSON, viewer, org_id, asset_id=p.id,
    )
    assert masked["email"] == "alice@example.com"
    # phone still sensitive (code default).
    assert masked["phone"] == "[redacted]"


@pytest.mark.asyncio
async def test_other_assets_unaffected_by_per_asset_override(pool, viewer):
    org_id = await organization_service.get_default_org_id(pool)
    a = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="A",
        metadata_={"email": "a@x"},
    )
    b = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="B",
        metadata_={"email": "b@x"},
    )
    # Declassify A.email only.
    await redaction.upsert_asset_override(pool, a.id, "email", False)
    a_masked = await redaction.redact_metadata_async(
        pool, a.metadata_, AssetType.PERSON, viewer, org_id, asset_id=a.id,
    )
    b_masked = await redaction.redact_metadata_async(
        pool, b.metadata_, AssetType.PERSON, viewer, org_id, asset_id=b.id,
    )
    assert a_masked["email"] == "a@x"
    assert b_masked["email"] == "[redacted]"


@pytest.mark.asyncio
async def test_clear_override_reverts(pool, viewer):
    org_id = await organization_service.get_default_org_id(pool)
    a = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="X", metadata_={"environment": "prod"},
    )
    await redaction.upsert_asset_override(pool, a.id, "environment", True)
    await redaction.clear_asset_override(pool, a.id, "environment")
    masked = await redaction.redact_metadata_async(
        pool, a.metadata_, AssetType.SYSTEM, viewer, org_id, asset_id=a.id,
    )
    assert masked["environment"] == "prod"


@pytest.mark.asyncio
async def test_admin_form_persists_overrides(pool, auth_client):
    a = await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Form", metadata_={"environment": "prod"},
    )
    resp = await auth_client.post(
        f"/assets/{a.id}/sensitive-overrides",
        data={"override.environment": "sensitive"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    overrides = await redaction.list_asset_overrides(pool, a.id)
    assert overrides == {"environment": True}
