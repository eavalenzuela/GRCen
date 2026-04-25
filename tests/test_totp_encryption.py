"""TOTP secrets encryption-at-rest scope."""
import base64
import secrets
import uuid

import pyotp
import pytest

from grcen.config import settings
from grcen.permissions import UserRole
from grcen.services import encryption, encryption_config, totp_service
from grcen.services.auth import create_user


@pytest.fixture
def encryption_on(monkeypatch):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)
    monkeypatch.setattr(encryption, "_initialised", False)
    monkeypatch.setattr(encryption, "_engine", None)
    yield


@pytest.mark.asyncio
async def test_totp_secret_stored_encrypted_when_scope_active(pool, encryption_on):
    await encryption_config.set_profile(pool, "custom", ["totp_secrets"])
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN)
    await totp_service.begin_enrollment(pool, user.id)
    raw = await pool.fetchval("SELECT secret FROM user_totp WHERE user_id = $1", user.id)
    assert raw.startswith("enc:1:")
    # The decrypted form is what the service hands back.
    enrollment = await totp_service.get_enrollment(pool, user.id)
    assert enrollment is not None
    assert not enrollment["secret"].startswith("enc:")


@pytest.mark.asyncio
async def test_totp_works_end_to_end_with_encryption(pool, encryption_on):
    """Enrolment → confirm → verify all keep working when secrets are encrypted."""
    await encryption_config.set_profile(pool, "custom", ["totp_secrets"])
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN)
    secret, _ = await totp_service.begin_enrollment(pool, user.id)
    code = pyotp.TOTP(secret).now()
    assert await totp_service.confirm_enrollment(pool, user.id, code)
    assert await totp_service.verify_login_code(pool, user.id, pyotp.TOTP(secret).now())


@pytest.mark.asyncio
async def test_totp_secret_plaintext_when_scope_inactive(pool):
    """Without the scope active the secret stays plaintext (back-compat)."""
    user = await create_user(pool, f"u_{uuid.uuid4().hex[:8]}", "x", role=UserRole.ADMIN)
    await totp_service.begin_enrollment(pool, user.id)
    raw = await pool.fetchval("SELECT secret FROM user_totp WHERE user_id = $1", user.id)
    assert not raw.startswith("enc:")


@pytest.mark.asyncio
async def test_scope_appears_in_all_scopes_registry():
    from grcen.services.encryption_scopes import ALL_SCOPES
    assert "totp_secrets" in ALL_SCOPES
    scope = ALL_SCOPES["totp_secrets"]
    assert any(t.table == "user_totp" and t.column == "secret" for t in scope.targets)
