"""Application-level field encryption using AES-GCM.

Encryption is optional.  When ``ENCRYPTION_KEY`` is empty every public
helper is a transparent no-op so the rest of the application never needs
to branch on "is encryption configured?".

Ciphertext format (text columns)::

    enc:1:<base64url(nonce[12] || ciphertext || tag[16])>

The ``enc:`` prefix lets callers distinguish encrypted values from
plaintext during mixed-state migrations.  The ``1`` is a version tag
reserved for future algorithm changes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from grcen.config import settings

ENCRYPTED_PREFIX = "enc:1:"
_INFO = b"grcen-field-encryption"


class EncryptionEngine:
    """Derives per-scope AES-256-GCM keys from a master key via HKDF."""

    def __init__(self, master_key_b64: str, retired_key_b64: str = ""):
        self._active_key = base64.urlsafe_b64decode(master_key_b64)
        if len(self._active_key) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        self._retired_key: bytes | None = None
        if retired_key_b64:
            self._retired_key = base64.urlsafe_b64decode(retired_key_b64)
            if len(self._retired_key) != 32:
                raise ValueError("ENCRYPTION_KEY_RETIRED must decode to exactly 32 bytes")
        self._scope_keys: dict[str, AESGCM] = {}
        self._retired_scope_keys: dict[str, AESGCM] = {}

    # -- key derivation ----------------------------------------------------

    @staticmethod
    def _derive(master: bytes, scope: str) -> AESGCM:
        derived = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=scope.encode(),
            info=_INFO,
        ).derive(master)
        return AESGCM(derived)

    def _get_key(self, scope: str) -> AESGCM:
        if scope not in self._scope_keys:
            self._scope_keys[scope] = self._derive(self._active_key, scope)
        return self._scope_keys[scope]

    def _get_retired_key(self, scope: str) -> AESGCM | None:
        if self._retired_key is None:
            return None
        if scope not in self._retired_scope_keys:
            self._retired_scope_keys[scope] = self._derive(self._retired_key, scope)
        return self._retired_scope_keys[scope]

    # -- encrypt / decrypt -------------------------------------------------

    def encrypt(self, plaintext: str, scope: str) -> str:
        """Encrypt *plaintext* under *scope*, returning a prefixed string."""
        nonce = os.urandom(12)
        ct = self._get_key(scope).encrypt(nonce, plaintext.encode(), scope.encode())
        blob = base64.urlsafe_b64encode(nonce + ct).decode()
        return f"{ENCRYPTED_PREFIX}{blob}"

    def decrypt(self, value: str, scope: str) -> str:
        """Decrypt *value*.  Returns plaintext unchanged if not encrypted."""
        if not value.startswith(ENCRYPTED_PREFIX):
            return value
        raw = base64.urlsafe_b64decode(value[len(ENCRYPTED_PREFIX) :])
        nonce, ct = raw[:12], raw[12:]
        # Try active key first.
        try:
            return self._get_key(scope).decrypt(nonce, ct, scope.encode()).decode()
        except Exception:
            pass
        # Fall back to retired key.
        retired = self._get_retired_key(scope)
        if retired is not None:
            return retired.decrypt(nonce, ct, scope.encode()).decode()
        raise ValueError(f"Decryption failed — no valid key for scope {scope!r}")

    # -- encrypt / decrypt raw bytes (for files) ---------------------------

    def encrypt_bytes(self, data: bytes, scope: str) -> bytes:
        """Encrypt raw bytes, returning ``nonce || ciphertext || tag``."""
        nonce = os.urandom(12)
        ct = self._get_key(scope).encrypt(nonce, data, scope.encode())
        return nonce + ct

    def decrypt_bytes(self, data: bytes, scope: str) -> bytes:
        """Decrypt raw bytes produced by :meth:`encrypt_bytes`."""
        nonce, ct = data[:12], data[12:]
        try:
            return self._get_key(scope).decrypt(nonce, ct, scope.encode())
        except Exception:
            pass
        retired = self._get_retired_key(scope)
        if retired is not None:
            return retired.decrypt(nonce, ct, scope.encode())
        raise ValueError(f"Decryption failed — no valid key for scope {scope!r}")

    # -- blind index -------------------------------------------------------

    def blind_index(self, value: str) -> str:
        """HMAC-SHA256 blind index for equality lookups on encrypted fields."""
        normalised = value.strip().lower()
        return _hmac.new(
            self._active_key, normalised.encode(), hashlib.sha256
        ).hexdigest()


# ── module-level singleton ────────────────────────────────────────────────

_engine: EncryptionEngine | None = None
_initialised = False


def _init_engine() -> EncryptionEngine | None:
    global _engine, _initialised
    _initialised = True
    if not settings.ENCRYPTION_KEY:
        _engine = None
        return None
    _engine = EncryptionEngine(settings.ENCRYPTION_KEY, settings.ENCRYPTION_KEY_RETIRED)
    return _engine


def get_engine() -> EncryptionEngine | None:
    """Return the singleton engine, or ``None`` if encryption is disabled."""
    if not _initialised:
        _init_engine()
    return _engine


# ── public helpers (no-op when encryption is disabled) ────────────────────


def encrypt_field(value: str, scope: str) -> str:
    engine = get_engine()
    if engine is None:
        return value
    return engine.encrypt(value, scope)


def decrypt_field(value: str, scope: str) -> str:
    engine = get_engine()
    if engine is None:
        return value
    return engine.decrypt(value, scope)


def encrypt_bytes(data: bytes, scope: str) -> bytes:
    engine = get_engine()
    if engine is None:
        return data
    return engine.encrypt_bytes(data, scope)


def decrypt_bytes(data: bytes, scope: str) -> bytes:
    engine = get_engine()
    if engine is None:
        return data
    return engine.decrypt_bytes(data, scope)


def blind_index(value: str) -> str | None:
    """Return an HMAC blind index, or ``None`` when encryption is off."""
    engine = get_engine()
    if engine is None:
        return None
    return engine.blind_index(value)


def is_encryption_enabled() -> bool:
    return get_engine() is not None
