"""Tests for the encryption-at-rest feature."""

import base64
import secrets

import pytest

from grcen.services.encryption import (
    ENCRYPTED_PREFIX,
    EncryptionEngine,
    blind_index,
    decrypt_field,
    encrypt_field,
    is_encryption_enabled,
)
from grcen.services.encryption_scopes import ALL_PROFILES, ALL_SCOPES

# ── helpers ───────────────────────────────────────────────────────────────


def _random_key_b64() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


# ── EncryptionEngine unit tests ───────────────────────────────────────────


class TestEncryptionEngine:
    def test_round_trip(self):
        engine = EncryptionEngine(_random_key_b64())
        ct = engine.encrypt("hello world", "test_scope")
        assert ct.startswith(ENCRYPTED_PREFIX)
        assert engine.decrypt(ct, "test_scope") == "hello world"

    def test_plaintext_passthrough(self):
        engine = EncryptionEngine(_random_key_b64())
        assert engine.decrypt("not encrypted", "test_scope") == "not encrypted"

    def test_different_scopes_produce_different_ciphertext(self):
        engine = EncryptionEngine(_random_key_b64())
        ct1 = engine.encrypt("same", "scope_a")
        ct2 = engine.encrypt("same", "scope_b")
        # Ciphertexts differ because of different derived keys AND random nonces.
        assert ct1 != ct2

    def test_wrong_scope_fails(self):
        engine = EncryptionEngine(_random_key_b64())
        ct = engine.encrypt("secret", "scope_a")
        with pytest.raises(Exception):
            engine.decrypt(ct, "scope_b")

    def test_key_rotation(self):
        old_key = _random_key_b64()
        new_key = _random_key_b64()

        old_engine = EncryptionEngine(old_key)
        ct = old_engine.encrypt("rotated", "scope")

        # New engine with old key as retired can still decrypt.
        new_engine = EncryptionEngine(new_key, retired_key_b64=old_key)
        assert new_engine.decrypt(ct, "scope") == "rotated"

    def test_re_encrypt_after_rotation(self):
        old_key = _random_key_b64()
        new_key = _random_key_b64()

        old_engine = EncryptionEngine(old_key)
        old_ct = old_engine.encrypt("data", "scope")

        # Decrypt with retired key, re-encrypt with new.
        new_engine = EncryptionEngine(new_key, retired_key_b64=old_key)
        plaintext = new_engine.decrypt(old_ct, "scope")
        new_ct = new_engine.encrypt(plaintext, "scope")

        # Fresh engine with only the new key can decrypt.
        fresh_engine = EncryptionEngine(new_key)
        assert fresh_engine.decrypt(new_ct, "scope") == "data"

    def test_bytes_round_trip(self):
        engine = EncryptionEngine(_random_key_b64())
        data = b"\x00\x01\x02\xff" * 100
        ct = engine.encrypt_bytes(data, "files")
        assert ct != data
        assert engine.decrypt_bytes(ct, "files") == data

    def test_blind_index_deterministic(self):
        engine = EncryptionEngine(_random_key_b64())
        idx1 = engine.blind_index("test@example.com")
        idx2 = engine.blind_index("test@example.com")
        assert idx1 == idx2

    def test_blind_index_case_insensitive(self):
        engine = EncryptionEngine(_random_key_b64())
        idx1 = engine.blind_index("Test@Example.COM")
        idx2 = engine.blind_index("test@example.com")
        assert idx1 == idx2

    def test_blind_index_different_values(self):
        engine = EncryptionEngine(_random_key_b64())
        idx1 = engine.blind_index("a@b.com")
        idx2 = engine.blind_index("c@d.com")
        assert idx1 != idx2

    def test_invalid_key_length_rejected(self):
        short_key = base64.urlsafe_b64encode(b"too_short").decode()
        with pytest.raises(ValueError, match="32 bytes"):
            EncryptionEngine(short_key)

    def test_invalid_retired_key_length_rejected(self):
        good = _random_key_b64()
        bad = base64.urlsafe_b64encode(b"short").decode()
        with pytest.raises(ValueError, match="32 bytes"):
            EncryptionEngine(good, retired_key_b64=bad)

    def test_empty_string_round_trip(self):
        engine = EncryptionEngine(_random_key_b64())
        ct = engine.encrypt("", "scope")
        assert engine.decrypt(ct, "scope") == ""

    def test_unicode_round_trip(self):
        engine = EncryptionEngine(_random_key_b64())
        text = "Guten Tag! \U0001f512 \u00e9\u00e8\u00ea"
        ct = engine.encrypt(text, "scope")
        assert engine.decrypt(ct, "scope") == text


# ── module-level helpers ──────────────────────────────────────────────────


class TestModuleHelpers:
    def test_disabled_passthrough(self):
        """When no key is configured, encrypt/decrypt are no-ops."""
        # The test env doesn't set ENCRYPTION_KEY, so this should pass through.
        assert encrypt_field("plain", "any") == "plain"
        assert decrypt_field("plain", "any") == "plain"
        assert blind_index("test") is None
        assert not is_encryption_enabled()


# ── scope & profile definitions ───────────────────────────────────────────


class TestScopesAndProfiles:
    def test_all_profile_scopes_exist(self):
        """Every scope referenced by a profile must exist in ALL_SCOPES."""
        for profile in ALL_PROFILES.values():
            for scope_name in profile.scope_names:
                assert scope_name in ALL_SCOPES, (
                    f"Profile {profile.name!r} references non-existent scope {scope_name!r}"
                )

    def test_gdpr_profile_covers_pii(self):
        gdpr = ALL_PROFILES["gdpr"]
        assert "user_pii" in gdpr.scope_names
        assert "session_pii" in gdpr.scope_names
        assert "audit_pii" in gdpr.scope_names

    def test_full_profile_covers_everything(self):
        full = ALL_PROFILES["full"]
        for scope_name in ALL_SCOPES:
            assert scope_name in full.scope_names

    def test_sso_secrets_scope_targets_client_secret(self):
        scope = ALL_SCOPES["sso_secrets"]
        has_client_secret = any(
            "client_secret" in t.filter_key_values for t in scope.targets
        )
        assert has_client_secret


# ── integration tests (require database) ──────────────────────────────────


class TestEncryptionConfig:
    @pytest.fixture(autouse=True)
    def _setup_engine(self, monkeypatch):
        """Temporarily enable encryption with a test key."""
        import grcen.services.encryption as enc_mod

        key = _random_key_b64()
        monkeypatch.setattr(enc_mod, "_engine", EncryptionEngine(key))
        monkeypatch.setattr(enc_mod, "_initialised", True)
        yield
        monkeypatch.setattr(enc_mod, "_engine", None)
        monkeypatch.setattr(enc_mod, "_initialised", False)

    async def test_set_profile_stores_scopes(self, pool):
        from grcen.services import encryption_config

        scopes = await encryption_config.set_profile(pool, "gdpr")
        assert "user_pii" in scopes
        assert "sso_secrets" in scopes

        active = await encryption_config.get_active_scopes(pool)
        assert active == scopes

    async def test_custom_profile(self, pool):
        from grcen.services import encryption_config

        scopes = await encryption_config.set_profile(
            pool, "custom", ["sso_secrets", "file_contents"]
        )
        assert scopes == {"sso_secrets", "file_contents"}

    async def test_clear_disables_all(self, pool):
        from grcen.services import encryption_config

        await encryption_config.set_profile(pool, "gdpr")
        await encryption_config.clear(pool)
        assert await encryption_config.get_active_scopes(pool) == set()

    async def test_oidc_secret_encrypted(self, pool):
        """When sso_secrets scope is active, client_secret is encrypted in DB."""
        from grcen.services import encryption_config, oidc_settings

        await encryption_config.set_profile(pool, "minimal")
        oidc_settings._cache = None  # clear cache

        await oidc_settings.update_settings(pool, client_secret="my-super-secret")

        # Read raw value from DB — should be ciphertext.
        raw = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw.startswith(ENCRYPTED_PREFIX)

        # Service layer should return plaintext.
        oidc_settings._cache = None
        cfg = await oidc_settings.get_settings(pool)
        assert cfg.client_secret == "my-super-secret"

    async def test_oidc_secret_plaintext_when_disabled(self, pool):
        """When no scopes are active, client_secret stays plaintext."""
        from grcen.services import encryption_config, oidc_settings

        await encryption_config.clear(pool)
        oidc_settings._cache = None

        await oidc_settings.update_settings(pool, client_secret="plain-secret")
        raw = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw == "plain-secret"

    async def test_user_email_encrypted(self, pool):
        """When user_pii scope is active, email is encrypted in DB."""
        from grcen.services import encryption_config
        from grcen.services.auth import create_oidc_user, get_user_by_email

        await encryption_config.set_profile(pool, "gdpr")

        user = await create_oidc_user(
            pool, "ssouser", "test@example.com", "oidc-sub-123"
        )
        assert user.email == "test@example.com"

        # Raw DB should have ciphertext.
        raw = await pool.fetchval("SELECT email FROM users WHERE id = $1", user.id)
        assert raw.startswith(ENCRYPTED_PREFIX)

        # Blind index should be populated.
        idx = await pool.fetchval(
            "SELECT email_blind_idx FROM users WHERE id = $1", user.id
        )
        assert idx is not None and len(idx) == 64

        # Lookup by email should work via blind index.
        found = await get_user_by_email(pool, "test@example.com")
        assert found is not None
        assert found.id == user.id
        assert found.email == "test@example.com"

    async def test_session_ip_encrypted(self, pool):
        """When session_pii scope is active, IP is encrypted in DB."""
        from grcen.services import encryption_config
        from grcen.services.auth import create_user
        from grcen.services.session_service import create_session

        await encryption_config.set_profile(pool, "gdpr")
        user = await create_user(pool, "sessuser", "testpass")
        sid = await create_session(pool, user.id, ip_address="192.168.1.1")

        raw = await pool.fetchval(
            "SELECT ip_address FROM sessions WHERE session_id = $1", sid
        )
        assert raw.startswith(ENCRYPTED_PREFIX)

    async def test_migrate_scope_encrypts_existing_data(self, pool):
        """Migration encrypts existing plaintext values."""
        from grcen.services import encryption_config, oidc_settings
        from grcen.services.encryption_migrate import migrate_scope

        # Write a secret in plaintext (no scope active).
        await encryption_config.clear(pool)
        oidc_settings._cache = None
        await oidc_settings.update_settings(pool, client_secret="pre-existing")
        raw_before = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw_before == "pre-existing"

        # Now activate and migrate.
        await encryption_config.set_profile(pool, "minimal")
        count = await migrate_scope(pool, "sso_secrets", encrypt=True)
        assert count >= 1

        raw_after = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw_after.startswith(ENCRYPTED_PREFIX)

    async def test_migrate_scope_decrypts_on_disable(self, pool):
        """Migration decrypts ciphertext when a scope is disabled."""
        from grcen.services import encryption_config, oidc_settings
        from grcen.services.encryption_migrate import migrate_scope

        await encryption_config.set_profile(pool, "minimal")
        oidc_settings._cache = None
        await oidc_settings.update_settings(pool, client_secret="to-be-decrypted")

        raw = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw.startswith(ENCRYPTED_PREFIX)

        count = await migrate_scope(pool, "sso_secrets", encrypt=False)
        assert count >= 1

        raw_after = await pool.fetchval(
            "SELECT value FROM oidc_config WHERE key = 'client_secret'"
        )
        assert raw_after == "to-be-decrypted"
