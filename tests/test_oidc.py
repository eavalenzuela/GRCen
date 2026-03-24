"""Tests for OIDC/SSO integration."""

import json

import pytest

from grcen.permissions import UserRole
from grcen.routers.oidc import resolve_role
from grcen.services import auth as auth_svc
from grcen.services import oidc_settings
from grcen.services.oidc_settings import OIDCSettings


# --- resolve_role unit tests ---


class TestResolveRole:
    """Test OIDC claim -> GRCen role mapping."""

    def _cfg(self, claim="groups", mapping=None, default="viewer"):
        return OIDCSettings(
            role_claim=claim,
            role_mapping=json.dumps(mapping or {}),
            default_role=default,
        )

    def test_empty_mapping_returns_default(self):
        assert resolve_role({"groups": ["foo"]}, self._cfg(default="editor")) == UserRole.EDITOR

    def test_flat_string_claim(self):
        assert resolve_role({"groups": "admins"}, self._cfg(mapping={"admins": "admin"})) == UserRole.ADMIN

    def test_list_claim(self):
        assert resolve_role({"groups": ["users", "editors"]}, self._cfg(mapping={"editors": "editor"})) == UserRole.EDITOR

    def test_dot_path_claim(self):
        cfg = self._cfg(claim="realm_access.roles", mapping={"grcen-admin": "admin"})
        userinfo = {"realm_access": {"roles": ["grcen-admin"]}}
        assert resolve_role(userinfo, cfg) == UserRole.ADMIN

    def test_missing_claim_returns_default(self):
        cfg = self._cfg(mapping={"admins": "admin"}, default="viewer")
        assert resolve_role({"no_groups": "here"}, cfg) == UserRole.VIEWER

    def test_highest_privilege_wins(self):
        cfg = self._cfg(mapping={"viewers": "viewer", "editors": "editor", "admins": "admin"})
        assert resolve_role({"groups": ["viewers", "editors", "admins"]}, cfg) == UserRole.ADMIN

    def test_no_matching_group_returns_default(self):
        cfg = self._cfg(mapping={"admins": "admin"}, default="auditor")
        assert resolve_role({"groups": ["unrelated"]}, cfg) == UserRole.AUDITOR

    def test_invalid_role_in_mapping_skipped(self):
        cfg = self._cfg(mapping={"group1": "nonexistent"}, default="viewer")
        assert resolve_role({"groups": ["group1"]}, cfg) == UserRole.VIEWER


# --- Auth service OIDC functions ---


@pytest.mark.asyncio
async def test_create_oidc_user(pool):
    user = await auth_svc.create_oidc_user(pool, "sso_user", "sso@example.com", "oidc-sub-123")
    assert user.username == "sso_user"
    assert user.email == "sso@example.com"
    assert user.oidc_sub == "oidc-sub-123"
    assert user.is_sso is True
    assert user.hashed_password == "!unusable"
    assert user.role == UserRole.VIEWER


@pytest.mark.asyncio
async def test_create_oidc_user_with_role(pool):
    user = await auth_svc.create_oidc_user(
        pool, "sso_admin", "admin@example.com", "oidc-sub-456", role=UserRole.ADMIN
    )
    assert user.role == UserRole.ADMIN
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_get_user_by_oidc_sub(pool):
    await auth_svc.create_oidc_user(pool, "lookup_user", "lu@example.com", "sub-lookup")
    found = await auth_svc.get_user_by_oidc_sub(pool, "sub-lookup")
    assert found is not None
    assert found.username == "lookup_user"

    missing = await auth_svc.get_user_by_oidc_sub(pool, "sub-nonexistent")
    assert missing is None


@pytest.mark.asyncio
async def test_get_user_by_email(pool):
    await auth_svc.create_oidc_user(pool, "email_user", "find@example.com", "sub-email")
    found = await auth_svc.get_user_by_email(pool, "find@example.com")
    assert found is not None
    assert found.username == "email_user"


@pytest.mark.asyncio
async def test_update_oidc_user(pool):
    user = await auth_svc.create_oidc_user(pool, "upd_user", "old@example.com", "sub-upd")
    updated = await auth_svc.update_oidc_user(
        pool, user.id, email="new@example.com", role=UserRole.EDITOR
    )
    assert updated.email == "new@example.com"
    assert updated.role == UserRole.EDITOR


@pytest.mark.asyncio
async def test_set_person_asset_link(pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    user = await auth_svc.create_oidc_user(pool, "link_user", "link@example.com", "sub-link")
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Link Person", status="active"
    )

    updated = await auth_svc.set_person_asset_link(pool, user.id, person.id)
    assert updated.person_asset_id == person.id

    # Clear the link
    cleared = await auth_svc.set_person_asset_link(pool, user.id, None)
    assert cleared.person_asset_id is None


@pytest.mark.asyncio
async def test_sso_user_cannot_login_locally(pool):
    await auth_svc.create_oidc_user(pool, "sso_only", "sso@test.com", "sub-nopw")
    result = await auth_svc.authenticate_user(pool, "sso_only", "anypassword")
    assert result is None


# --- OIDC settings service ---


@pytest.mark.asyncio
async def test_oidc_settings_defaults(pool):
    oidc_settings._cache = None
    cfg = await oidc_settings.get_settings(pool)
    assert cfg.issuer_url == ""
    assert cfg.enabled is False
    assert cfg.display_name == "SSO"


@pytest.mark.asyncio
async def test_oidc_settings_update_and_reload(pool):
    oidc_settings._cache = None
    cfg = await oidc_settings.update_settings(
        pool,
        issuer_url="https://idp.example.com",
        client_id="test-client",
        client_secret="test-secret",
        display_name="Company SSO",
    )
    assert cfg.enabled is True
    assert cfg.display_name == "Company SSO"

    # Reload from DB
    cfg2 = await oidc_settings.reload(pool)
    assert cfg2.issuer_url == "https://idp.example.com"
    assert cfg2.enabled is True


# --- Login page shows SSO button only when enabled ---


@pytest.mark.asyncio
async def test_login_page_no_sso_button_by_default(client, pool):
    oidc_settings._cache = None
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in with" not in resp.content


@pytest.mark.asyncio
async def test_login_page_shows_sso_button(client, pool):
    oidc_settings._cache = None
    await oidc_settings.update_settings(
        pool,
        issuer_url="https://idp.example.com",
        client_id="test-client",
        client_secret="test-secret",
        display_name="Company SSO",
    )

    resp = await client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in with Company SSO" in resp.content
    assert b"/auth/oidc/login" in resp.content


# --- OIDC routes return 404 when disabled ---


@pytest.mark.asyncio
async def test_oidc_login_404_when_disabled(client, pool):
    oidc_settings._cache = None
    resp = await client.get("/auth/oidc/login")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_oidc_callback_404_when_disabled(client, pool):
    oidc_settings._cache = None
    resp = await client.get("/auth/oidc/callback")
    assert resp.status_code == 404


# --- Admin UI shows SSO vs Local ---


@pytest.mark.asyncio
async def test_admin_users_shows_auth_type(auth_client, pool):
    await auth_svc.create_oidc_user(pool, "sso_visible", "vis@example.com", "sub-vis")
    resp = await auth_client.get("/admin/users")
    assert resp.status_code == 200
    assert b"SSO" in resp.content
    assert b"Local" in resp.content


# --- Admin user edit shows person asset dropdown ---


@pytest.mark.asyncio
async def test_admin_user_edit_shows_person_dropdown(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    user = await auth_svc.create_oidc_user(pool, "edit_me", "edit@test.com", "sub-edit")
    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Editable Person", status="active"
    )
    resp = await auth_client.get(f"/admin/users/{user.id}/edit")
    assert resp.status_code == 200
    assert b"Linked Person Asset" in resp.content
    assert b"Editable Person" in resp.content


# --- Person asset detail shows linked user ---


@pytest.mark.asyncio
async def test_person_detail_shows_linked_user(auth_client, pool):
    from grcen.models.asset import AssetType
    from grcen.services import asset as asset_svc

    person = await asset_svc.create_asset(
        pool, type=AssetType.PERSON, name="Linked Person", status="active"
    )
    user = await auth_svc.create_oidc_user(pool, "linked_sso", "linked@test.com", "sub-linked")
    await auth_svc.set_person_asset_link(pool, user.id, person.id)

    resp = await auth_client.get(f"/assets/{person.id}")
    assert resp.status_code == 200
    assert b"User Account" in resp.content
    assert b"linked_sso" in resp.content


# --- Admin OIDC settings page ---


@pytest.mark.asyncio
async def test_admin_oidc_settings_page(auth_client, pool):
    resp = await auth_client.get("/admin/oidc-settings")
    assert resp.status_code == 200
    assert b"SSO Settings" in resp.content
    assert b"Issuer URL" in resp.content


@pytest.mark.asyncio
async def test_admin_oidc_settings_submit(auth_client, pool):
    oidc_settings._cache = None
    resp = await auth_client.post(
        "/admin/oidc-settings",
        data={
            "issuer_url": "https://new-idp.example.com",
            "client_id": "new-client",
            "client_secret": "new-secret",
            "scopes": "openid email profile",
            "role_claim": "groups",
            "role_mapping": '{"admins": "admin"}',
            "default_role": "editor",
            "display_name": "New SSO",
        },
    )
    assert resp.status_code == 302

    cfg = await oidc_settings.get_settings(pool)
    assert cfg.issuer_url == "https://new-idp.example.com"
    assert cfg.client_id == "new-client"
    assert cfg.default_role == "editor"
    assert cfg.display_name == "New SSO"
    assert cfg.enabled is True


@pytest.mark.asyncio
async def test_viewer_cannot_access_oidc_settings(viewer_client, pool):
    resp = await viewer_client.get("/admin/oidc-settings")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_users_page_has_sso_settings_link(auth_client, pool):
    resp = await auth_client.get("/admin/users")
    assert resp.status_code == 200
    assert b"SSO Settings" in resp.content
    assert b"/admin/oidc-settings" in resp.content
