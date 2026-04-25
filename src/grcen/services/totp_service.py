"""TOTP second-factor authentication for local (non-SSO) users.

pyotp handles the RFC 6238 math; this module owns the persistence, recovery
codes, and lookup/verification flow the login page needs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import io
import secrets
from uuid import UUID

import asyncpg
import pyotp
import qrcode

from grcen.config import settings
from grcen.services import encryption_config
from grcen.services.encryption import decrypt_field, encrypt_field

_ISSUER = "GRCen"
_TOTP_SCOPE = "totp_secrets"


async def _maybe_encrypt_secret(pool: asyncpg.Pool, secret: str) -> str:
    """Encrypt the TOTP secret when the totp_secrets scope is active."""
    if await encryption_config.is_scope_active(pool, _TOTP_SCOPE):
        return encrypt_field(secret, _TOTP_SCOPE)
    return secret


def _maybe_decrypt_secret(secret: str) -> str:
    """decrypt_field is a no-op on plaintext, so this is safe even when the
    scope hasn't been activated yet (mixed-state migration window)."""
    return decrypt_field(secret, _TOTP_SCOPE)


def generate_secret() -> str:
    """Return a base32 secret suitable for a pyotp TOTP."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    totp = pyotp.TOTP(secret)
    label = f"{settings.APP_NAME or 'GRCen'}:{username}"
    return totp.provisioning_uri(name=username, issuer_name=_ISSUER) if not label else totp.provisioning_uri(name=username, issuer_name=_ISSUER)


def qr_png_b64(secret: str, username: str) -> str:
    """Return a data-URL-ready base64 PNG of the provisioning QR code."""
    uri = provisioning_uri(secret, username)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def generate_recovery_codes(n: int = 8) -> list[str]:
    """Generate human-readable one-time recovery codes (stored hashed)."""
    return [secrets.token_hex(4).upper() + "-" + secrets.token_hex(4).upper() for _ in range(n)]


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    if not code or not code.strip().isdigit():
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window)


# ── persistence ──────────────────────────────────────────────────────────


async def get_enrollment(pool: asyncpg.Pool, user_id: UUID) -> dict | None:
    row = await pool.fetchrow(
        "SELECT secret, recovery_codes, enabled FROM user_totp WHERE user_id = $1",
        user_id,
    )
    if not row:
        return None
    return {
        "secret": _maybe_decrypt_secret(row["secret"]),
        "recovery_codes": list(row["recovery_codes"] or []),
        "enabled": row["enabled"],
    }


async def is_enabled(pool: asyncpg.Pool, user_id: UUID) -> bool:
    enrollment = await get_enrollment(pool, user_id)
    return bool(enrollment and enrollment["enabled"])


async def begin_enrollment(
    pool: asyncpg.Pool, user_id: UUID
) -> tuple[str, list[str]]:
    """Create (or reset) a pending enrollment. Returns (secret, recovery_codes_plain).

    Recovery codes are returned as plaintext once, here, so the UI can display
    them. The DB stores only their SHA-256 hashes.
    """
    secret = generate_secret()
    recovery_plain = generate_recovery_codes()
    recovery_hashed = [_hash_code(c) for c in recovery_plain]
    stored_secret = await _maybe_encrypt_secret(pool, secret)
    await pool.execute(
        """INSERT INTO user_totp (user_id, secret, recovery_codes, enabled)
           VALUES ($1, $2, $3, false)
           ON CONFLICT (user_id) DO UPDATE SET
               secret = EXCLUDED.secret,
               recovery_codes = EXCLUDED.recovery_codes,
               enabled = false,
               updated_at = now()""",
        user_id,
        stored_secret,
        recovery_hashed,
    )
    return secret, recovery_plain


async def confirm_enrollment(
    pool: asyncpg.Pool, user_id: UUID, code: str
) -> bool:
    """Verify a TOTP code against the pending secret and flip enabled=true."""
    enrollment = await get_enrollment(pool, user_id)
    if not enrollment:
        return False
    if not verify_totp(enrollment["secret"], code):
        return False
    await pool.execute(
        """UPDATE user_totp SET enabled = true, updated_at = now()
           WHERE user_id = $1""",
        user_id,
    )
    return True


async def disable(pool: asyncpg.Pool, user_id: UUID) -> None:
    await pool.execute("DELETE FROM user_totp WHERE user_id = $1", user_id)


async def verify_login_code(
    pool: asyncpg.Pool, user_id: UUID, code: str
) -> bool:
    """Accept either a TOTP code or a one-time recovery code. Constant-time.

    A matched recovery code is consumed (removed from the stored list).
    """
    enrollment = await get_enrollment(pool, user_id)
    if not enrollment or not enrollment["enabled"]:
        return False

    cleaned = (code or "").strip()
    if not cleaned:
        return False

    # Try TOTP first.
    if verify_totp(enrollment["secret"], cleaned):
        return True

    # Recovery code — hash, compare constant-time against each stored hash.
    submitted_hash = _hash_code(cleaned.upper())
    for stored in enrollment["recovery_codes"]:
        if _hmac.compare_digest(stored, submitted_hash):
            # Consume it.
            remaining = [c for c in enrollment["recovery_codes"] if c != stored]
            await pool.execute(
                """UPDATE user_totp SET recovery_codes = $1, updated_at = now()
                   WHERE user_id = $2""",
                remaining,
                user_id,
            )
            return True
    return False
