import hashlib
import secrets
import string
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.models.api_token import ApiToken

_PREFIX = "grcen_"
_TOKEN_BYTES = 48
_ALPHABET = string.ascii_letters + string.digits


def _generate_raw_token() -> str:
    rand = "".join(secrets.choice(_ALPHABET) for _ in range(_TOKEN_BYTES))
    return f"{_PREFIX}{rand}"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# App settings helpers
# ---------------------------------------------------------------------------

async def get_max_expiry_days(pool: asyncpg.Pool) -> int | None:
    row = await pool.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'token_max_expiry_days'"
    )
    if not row:
        return None
    try:
        return int(row["value"])
    except (ValueError, TypeError):
        return None


async def set_max_expiry_days(pool: asyncpg.Pool, days: int | None) -> None:
    if days is None:
        await pool.execute(
            "DELETE FROM app_settings WHERE key = 'token_max_expiry_days'"
        )
    else:
        await pool.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('token_max_expiry_days', $1, now())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            str(days),
        )


# ---------------------------------------------------------------------------
# Token CRUD
# ---------------------------------------------------------------------------

async def create_token(
    pool: asyncpg.Pool,
    user_id: UUID,
    name: str,
    permissions: list[str],
    expires_at: datetime | None = None,
    is_service_account: bool = False,
    allowed_ips: list[str] | None = None,
) -> tuple[ApiToken, str]:
    """Create a new API token. Returns (token_record, raw_token)."""
    if not is_service_account:
        max_days = await get_max_expiry_days(pool)
        if max_days is not None:
            cap = datetime.now(UTC) + timedelta(days=max_days)
            if expires_at is None or expires_at > cap:
                expires_at = cap

    raw = _generate_raw_token()
    token_hash = _hash_token(raw)

    # Token inherits the org of its owner so per-token requests stay tenant-scoped.
    org_row = await pool.fetchrow("SELECT organization_id FROM users WHERE id = $1", user_id)
    org_id = org_row["organization_id"] if org_row else None

    row = await pool.fetchrow(
        """INSERT INTO api_tokens (user_id, name, token_hash, permissions,
                                   expires_at, is_service_account, organization_id, allowed_ips)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           RETURNING *""",
        user_id,
        name,
        token_hash,
        permissions,
        expires_at,
        is_service_account,
        org_id,
        allowed_ips or [],
    )
    return ApiToken.from_row(row), raw


async def validate_token(
    pool: asyncpg.Pool, raw: str, *, client_ip: str | None = None
) -> ApiToken | None:
    """Look up a raw token string, returning the record if valid.

    When the token has a non-empty ``allowed_ips`` list, the caller's IP must
    appear in it. We accept exact-match strings for now — CIDR support can
    layer on top later.
    """
    token_hash = _hash_token(raw)
    row = await pool.fetchrow(
        "SELECT * FROM api_tokens WHERE token_hash = $1", token_hash
    )
    if not row:
        return None

    token = ApiToken.from_row(row)
    if token.revoked:
        return None
    if token.expires_at and token.expires_at < datetime.now(UTC):
        return None
    if token.allowed_ips and (client_ip is None or client_ip not in token.allowed_ips):
        return None

    await pool.execute(
        "UPDATE api_tokens SET last_used_at = now() WHERE id = $1", token.id
    )
    return token


async def update_allowed_ips(
    pool: asyncpg.Pool, token_id: UUID, allowed_ips: list[str]
) -> bool:
    result = await pool.execute(
        "UPDATE api_tokens SET allowed_ips = $1 WHERE id = $2 AND revoked = false",
        allowed_ips, token_id,
    )
    return result == "UPDATE 1"


async def list_tokens_for_user(
    pool: asyncpg.Pool, user_id: UUID
) -> list[ApiToken]:
    rows = await pool.fetch(
        "SELECT * FROM api_tokens WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )
    return [ApiToken.from_row(r) for r in rows]


async def list_all_tokens(pool: asyncpg.Pool) -> list[ApiToken]:
    rows = await pool.fetch(
        "SELECT * FROM api_tokens ORDER BY created_at DESC"
    )
    return [ApiToken.from_row(r) for r in rows]


async def get_token_by_id(pool: asyncpg.Pool, token_id: UUID) -> ApiToken | None:
    row = await pool.fetchrow("SELECT * FROM api_tokens WHERE id = $1", token_id)
    return ApiToken.from_row(row) if row else None


async def revoke_token(pool: asyncpg.Pool, token_id: UUID) -> bool:
    result = await pool.execute(
        "UPDATE api_tokens SET revoked = true WHERE id = $1 AND revoked = false",
        token_id,
    )
    return result == "UPDATE 1"
