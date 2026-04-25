"""Per-org email branding overrides default rendering."""
import uuid

import pytest

from grcen.config import settings
from grcen.models.alert import Alert
from grcen.services import email_service, organization_service


def _alert():
    return Alert(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        title="Test",
        message="Body",
        schedule_type="once",
        cron_expression=None,
        next_fire_at=None,
        enabled=True,
        created_at=None,
        updated_at=None,
    )


def test_render_without_org_uses_app_defaults():
    text, html = email_service.render_alert_email(_alert(), "asset", "https://x")
    assert settings.APP_NAME in text
    assert "#1f2937" in html  # default brand color


@pytest.mark.asyncio
async def test_render_with_org_branding(pool):
    org = await organization_service.create_organization(
        pool, slug=f"brand_{uuid.uuid4().hex[:6]}", name="Brand Org"
    )
    await organization_service.update_branding(
        pool,
        org.id,
        email_from_name="Acme Compliance",
        email_brand_color="#ff8800",
        email_logo_url="https://example.com/logo.png",
    )
    refreshed = await organization_service.get_by_id(pool, org.id)
    text, html = email_service.render_alert_email(
        _alert(), "asset", "https://x", org=refreshed
    )
    assert "Acme Compliance" in text
    assert "Acme Compliance" in html
    assert "#ff8800" in html
    assert "https://example.com/logo.png" in html


@pytest.mark.asyncio
async def test_partial_branding_falls_back_per_field(pool):
    """Only setting one field still works — the rest revert to defaults."""
    org = await organization_service.create_organization(
        pool, slug=f"partial_{uuid.uuid4().hex[:6]}", name="Partial"
    )
    await organization_service.update_branding(
        pool, org.id, email_brand_color="#aabbcc",
    )
    refreshed = await organization_service.get_by_id(pool, org.id)
    text, html = email_service.render_alert_email(
        _alert(), "asset", "https://x", org=refreshed
    )
    assert "#aabbcc" in html
    # From-name was blank → falls back to APP_NAME
    assert settings.APP_NAME in text


@pytest.mark.asyncio
async def test_branding_form_persists(pool, auth_client):
    resp = await auth_client.post(
        "/admin/organization/branding",
        data={
            "email_from_name": "Form Brand",
            "email_brand_color": "#112233",
            "email_logo_url": "https://example.com/x.png",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    org_id = (
        await pool.fetchrow("SELECT id FROM organizations WHERE slug = 'default'")
    )["id"]
    org = await organization_service.get_by_id(pool, org_id)
    assert org.email_from_name == "Form Brand"
    assert org.email_brand_color == "#112233"
    assert org.email_logo_url == "https://example.com/x.png"
