"""Per-org overrides for the custom-field sensitive flag."""
import pytest

from grcen.models.asset import AssetType
from grcen.services import organization_service, redaction


@pytest.mark.asyncio
async def test_code_default_used_when_no_override(pool):
    org_id = await organization_service.get_default_org_id(pool)
    fields = await redaction.effective_sensitive_field_names(
        pool, AssetType.PERSON, org_id
    )
    # email/phone/clearance_level are marked sensitive in custom_fields.py.
    assert "email" in fields
    assert "phone" in fields


@pytest.mark.asyncio
async def test_override_promotes_field_to_sensitive(pool):
    org_id = await organization_service.get_default_org_id(pool)
    # 'environment' on System is non-sensitive by default — promote it.
    await redaction.upsert_override(
        pool, org_id, AssetType.SYSTEM, "environment", sensitive=True
    )
    fields = await redaction.effective_sensitive_field_names(
        pool, AssetType.SYSTEM, org_id
    )
    assert "environment" in fields


@pytest.mark.asyncio
async def test_override_can_declassify(pool):
    """A sensitive=False override removes a code-default sensitive field."""
    org_id = await organization_service.get_default_org_id(pool)
    await redaction.upsert_override(
        pool, org_id, AssetType.PERSON, "email", sensitive=False
    )
    fields = await redaction.effective_sensitive_field_names(
        pool, AssetType.PERSON, org_id
    )
    assert "email" not in fields


@pytest.mark.asyncio
async def test_clear_override_reverts_to_code_default(pool):
    org_id = await organization_service.get_default_org_id(pool)
    await redaction.upsert_override(
        pool, org_id, AssetType.PERSON, "email", sensitive=False
    )
    await redaction.clear_override(pool, org_id, AssetType.PERSON, "email")
    fields = await redaction.effective_sensitive_field_names(
        pool, AssetType.PERSON, org_id
    )
    assert "email" in fields


@pytest.mark.asyncio
async def test_admin_form_persists_override(pool, auth_client):
    """The admin form posts inherit/sensitive/public per row."""
    resp = await auth_client.post(
        "/admin/sensitive-fields",
        # Mark System.environment sensitive (it isn't by code default).
        data={"system.environment": "sensitive"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    overrides = await redaction.list_overrides(
        pool, await organization_service.get_default_org_id(pool)
    )
    assert overrides.get(("system", "environment")) is True


@pytest.mark.asyncio
async def test_sensitive_fields_page_lists_fields(auth_client):
    resp = await auth_client.get("/admin/sensitive-fields")
    assert resp.status_code == 200
    assert "Sensitive Field Overrides" in resp.text
    assert "email" in resp.text  # Person.email is a known sensitive field
