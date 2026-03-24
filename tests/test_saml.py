"""Tests for SAML 2.0 SSO integration."""

import json

import pytest

from grcen.permissions import UserRole
from grcen.routers.saml import resolve_role
from grcen.services import auth as auth_svc
from grcen.services import saml_settings
from grcen.services.saml_settings import SAMLSettings

# --- resolve_role unit tests ---


class TestSAMLResolveRole:
    """Test SAML attribute -> GRCen role mapping."""

    def _cfg(self, attribute="Role", mapping=None, default="viewer"):
        return SAMLSettings(
            role_attribute=attribute,
            role_mapping=json.dumps(mapping or {}),
            default_role=default,
        )

    def test_empty_mapping_returns_default(self):
        assert resolve_role({"Role": ["foo"]}, self._cfg(default="editor")) == UserRole.EDITOR

    def test_flat_string_attribute(self):
        cfg = self._cfg(mapping={"admins": "admin"})
        assert resolve_role({"Role": "admins"}, cfg) == UserRole.ADMIN

    def test_list_attribute(self):
        cfg = self._cfg(mapping={"editors": "editor"})
        assert resolve_role({"Role": ["users", "editors"]}, cfg) == UserRole.EDITOR

    def test_missing_attribute_returns_default(self):
        cfg = self._cfg(mapping={"admins": "admin"}, default="viewer")
        assert resolve_role({"other": ["stuff"]}, cfg) == UserRole.VIEWER

    def test_highest_privilege_wins(self):
        cfg = self._cfg(mapping={"viewers": "viewer", "editors": "editor", "admins": "admin"})
        assert resolve_role({"Role": ["viewers", "editors", "admins"]}, cfg) == UserRole.ADMIN

    def test_no_matching_group_returns_default(self):
        cfg = self._cfg(mapping={"admins": "admin"}, default="auditor")
        assert resolve_role({"Role": ["unrelated"]}, cfg) == UserRole.AUDITOR

    def test_invalid_role_in_mapping_skipped(self):
        cfg = self._cfg(mapping={"group1": "nonexistent"}, default="viewer")
        assert resolve_role({"Role": ["group1"]}, cfg) == UserRole.VIEWER

    def test_custom_attribute_name(self):
        cfg = self._cfg(attribute="memberOf", mapping={"dev-team": "editor"})
        assert resolve_role({"memberOf": ["dev-team"]}, cfg) == UserRole.EDITOR


# --- SAMLSettings dataclass ---


class TestSAMLSettingsEnabled:
    def test_disabled_by_default(self):
        cfg = SAMLSettings()
        assert cfg.enabled is False

    def test_enabled_when_idp_configured(self):
        cfg = SAMLSettings(
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
            idp_x509_cert="MIID...",
        )
        assert cfg.enabled is True

    def test_disabled_if_missing_cert(self):
        cfg = SAMLSettings(
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
        )
        assert cfg.enabled is False

    def test_assertions_signed_property(self):
        assert SAMLSettings(want_assertions_signed="true").assertions_signed is True
        assert SAMLSettings(want_assertions_signed="false").assertions_signed is False

    def test_name_id_encrypted_property(self):
        assert SAMLSettings(want_name_id_encrypted="true").name_id_encrypted is True
        assert SAMLSettings(want_name_id_encrypted="false").name_id_encrypted is False


# --- SAML settings service (DB-backed) ---


@pytest.mark.asyncio
async def test_saml_settings_defaults(pool):
    saml_settings._cache = None
    cfg = await saml_settings.get_settings(pool)
    assert cfg.idp_entity_id == ""
    assert cfg.enabled is False
    assert cfg.display_name == "SAML SSO"


@pytest.mark.asyncio
async def test_saml_settings_update_and_reload(pool):
    saml_settings._cache = None
    cfg = await saml_settings.update_settings(
        pool,
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/saml/sso",
        idp_x509_cert="MIIDxyz...",
        display_name="Corp SAML",
    )
    assert cfg.enabled is True
    assert cfg.display_name == "Corp SAML"

    cfg2 = await saml_settings.reload(pool)
    assert cfg2.idp_entity_id == "https://idp.example.com"
    assert cfg2.enabled is True


@pytest.mark.asyncio
async def test_saml_settings_cache(pool):
    saml_settings._cache = None
    cfg1 = await saml_settings.get_settings(pool)
    cfg2 = await saml_settings.get_settings(pool)
    assert cfg1 is cfg2  # same object from cache


# --- Auth service SAML functions ---


@pytest.mark.asyncio
async def test_create_saml_user(pool):
    user = await auth_svc.create_saml_user(pool, "saml_user", "saml@example.com", "saml-sub-123")
    assert user.username == "saml_user"
    assert user.email == "saml@example.com"
    assert user.saml_sub == "saml-sub-123"
    assert user.is_sso is True
    assert user.hashed_password == "!unusable"
    assert user.role == UserRole.VIEWER


@pytest.mark.asyncio
async def test_create_saml_user_with_role(pool):
    user = await auth_svc.create_saml_user(
        pool, "saml_admin", "admin@example.com", "saml-sub-456", role=UserRole.ADMIN
    )
    assert user.role == UserRole.ADMIN
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_get_user_by_saml_sub(pool):
    await auth_svc.create_saml_user(pool, "lookup_saml", "lu@example.com", "sub-saml-lookup")
    found = await auth_svc.get_user_by_saml_sub(pool, "sub-saml-lookup")
    assert found is not None
    assert found.username == "lookup_saml"

    missing = await auth_svc.get_user_by_saml_sub(pool, "sub-nonexistent")
    assert missing is None


@pytest.mark.asyncio
async def test_update_saml_user(pool):
    user = await auth_svc.create_saml_user(pool, "upd_saml", "old@example.com", "sub-saml-upd")
    updated = await auth_svc.update_saml_user(
        pool, user.id, email="new@example.com", role=UserRole.EDITOR
    )
    assert updated.email == "new@example.com"
    assert updated.role == UserRole.EDITOR


@pytest.mark.asyncio
async def test_saml_user_cannot_login_locally(pool):
    await auth_svc.create_saml_user(pool, "saml_only", "saml@test.com", "sub-saml-nopw")
    result = await auth_svc.authenticate_user(pool, "saml_only", "anypassword")
    assert result is None


# --- Login page shows SAML button ---


@pytest.mark.asyncio
async def test_login_page_no_saml_button_by_default(client, pool):
    saml_settings._cache = None
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert b"/auth/saml/login" not in resp.content


@pytest.mark.asyncio
async def test_login_page_shows_saml_button(client, pool):
    saml_settings._cache = None
    await saml_settings.update_settings(
        pool,
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/saml/sso",
        idp_x509_cert="MIIDxyz...",
        display_name="Corp SAML",
    )

    resp = await client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in with Corp SAML" in resp.content
    assert b"/auth/saml/login" in resp.content


# --- SAML routes return 404 when disabled ---


@pytest.mark.asyncio
async def test_saml_login_404_when_disabled(client, pool):
    saml_settings._cache = None
    resp = await client.get("/auth/saml/login")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_saml_sls_404_when_disabled(client, pool):
    saml_settings._cache = None
    resp = await client.get("/auth/saml/sls")
    assert resp.status_code == 404


# --- Admin SAML settings page ---


@pytest.mark.asyncio
async def test_admin_saml_settings_page(auth_client, pool):
    resp = await auth_client.get("/admin/saml-settings")
    assert resp.status_code == 200
    assert b"SSO Settings" in resp.content
    assert b"IdP Entity ID" in resp.content


@pytest.mark.asyncio
async def test_admin_saml_settings_submit(auth_client, pool):
    saml_settings._cache = None
    resp = await auth_client.post(
        "/admin/saml-settings",
        data={
            "idp_entity_id": "https://new-idp.example.com",
            "idp_sso_url": "https://new-idp.example.com/saml/sso",
            "idp_slo_url": "",
            "idp_x509_cert": "MIIDnew...",
            "sp_entity_id": "",
            "sp_x509_cert": "",
            "sp_private_key": "",
            "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "role_attribute": "groups",
            "role_mapping": '{"admins": "admin"}',
            "default_role": "editor",
            "display_name": "New SAML",
        },
    )
    assert resp.status_code == 302

    cfg = await saml_settings.reload(pool)
    assert cfg.idp_entity_id == "https://new-idp.example.com"
    assert cfg.role_attribute == "groups"
    assert cfg.default_role == "editor"
    assert cfg.display_name == "New SAML"
    assert cfg.enabled is True


@pytest.mark.asyncio
async def test_admin_saml_settings_preserves_private_key(auth_client, pool):
    """Submitting '********' placeholder should preserve existing key."""
    saml_settings._cache = None
    await saml_settings.update_settings(pool, sp_private_key="secret-key-data")

    resp = await auth_client.post(
        "/admin/saml-settings",
        data={
            "idp_entity_id": "https://idp.example.com",
            "idp_sso_url": "https://idp.example.com/sso",
            "idp_x509_cert": "MIID...",
            "sp_private_key": "********",
            "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "role_attribute": "Role",
            "role_mapping": "{}",
            "default_role": "viewer",
            "display_name": "SAML SSO",
        },
    )
    assert resp.status_code == 302

    cfg = await saml_settings.reload(pool)
    assert cfg.sp_private_key == "secret-key-data"


@pytest.mark.asyncio
async def test_viewer_cannot_access_saml_settings(viewer_client, pool):
    resp = await viewer_client.get("/admin/saml-settings")
    assert resp.status_code == 403


# --- _prepare_saml_settings unit test ---


def test_prepare_saml_settings_dict():
    from unittest.mock import MagicMock

    from grcen.routers.saml import _prepare_saml_settings

    cfg = SAMLSettings(
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_x509_cert="MIIDxyz...",
        sp_entity_id="https://app.example.com/saml/metadata",
        name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    )
    request = MagicMock()
    request.headers = {}
    request.url.scheme = "https"
    request.url.netloc = "app.example.com"

    result = _prepare_saml_settings(cfg, request)

    assert result["strict"] is True
    assert result["sp"]["entityId"] == "https://app.example.com/saml/metadata"
    assert result["sp"]["assertionConsumerService"]["url"] == "https://app.example.com/auth/saml/acs"
    assert result["idp"]["entityId"] == "https://idp.example.com"
    assert result["idp"]["singleSignOnService"]["url"] == "https://idp.example.com/sso"
    assert result["idp"]["x509cert"] == "MIIDxyz..."
    assert result["security"]["wantAssertionsSigned"] is True


def test_prepare_saml_settings_auto_entity_id():
    """When sp_entity_id is empty, it should use the metadata URL."""
    from unittest.mock import MagicMock

    from grcen.routers.saml import _prepare_saml_settings

    cfg = SAMLSettings(
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_x509_cert="MIID...",
        sp_entity_id="",  # empty
    )
    request = MagicMock()
    request.headers = {}
    request.url.scheme = "https"
    request.url.netloc = "app.example.com"

    result = _prepare_saml_settings(cfg, request)
    assert result["sp"]["entityId"] == "https://app.example.com/auth/saml/metadata"


def test_prepare_saml_settings_with_slo():
    from unittest.mock import MagicMock

    from grcen.routers.saml import _prepare_saml_settings

    cfg = SAMLSettings(
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_slo_url="https://idp.example.com/slo",
        idp_x509_cert="MIID...",
    )
    request = MagicMock()
    request.headers = {}
    request.url.scheme = "https"
    request.url.netloc = "app.example.com"

    result = _prepare_saml_settings(cfg, request)
    assert result["idp"]["singleLogoutService"]["url"] == "https://idp.example.com/slo"


def test_prepare_saml_settings_respects_forwarded_headers():
    from unittest.mock import MagicMock

    from grcen.routers.saml import _prepare_saml_settings

    cfg = SAMLSettings(
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_x509_cert="MIID...",
    )
    request = MagicMock()
    request.headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "proxy.example.com",
    }
    request.url.scheme = "http"
    request.url.netloc = "internal:8000"

    result = _prepare_saml_settings(cfg, request)
    assert result["sp"]["assertionConsumerService"]["url"] == "https://proxy.example.com/auth/saml/acs"
