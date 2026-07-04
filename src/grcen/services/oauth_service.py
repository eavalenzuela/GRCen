"""OAuth 2.0 client-credentials grant (RFC 6749 §4.4) for machine clients.

Long-lived static API tokens are a standing liability for integrations; this
flow lets a machine client exchange a ``client_id`` + ``client_secret`` for a
*short-lived*, permission-scoped bearer token minted through the existing
``api_tokens`` machinery — so every downstream check (permission scoping,
expiry, org binding, revocation) keeps working unchanged.

Clients are registered by an admin at /admin/oauth-clients. The secret is
bcrypt-hashed at rest (same as user passwords) and shown exactly once at
creation. Issued tokens are owned by the registering admin's user record —
the token inherits that user's org — and expire after ``token_ttl_seconds``
(default 1 hour, capped at 24h).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.services import token_service
from grcen.services.auth import hash_password, verify_password

_CLIENT_ID_PREFIX = "grcen_client_"
_MAX_TTL_SECONDS = 24 * 3600
_DEFAULT_TTL_SECONDS = 3600


@dataclass
class OAuthClient:
    id: UUID
    organization_id: UUID
    client_id: str
    name: str
    permissions: list[str]
    token_ttl_seconds: int
    enabled: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row) -> OAuthClient:
        return cls(
            id=row["id"],
            organization_id=row["organization_id"],
            client_id=row["client_id"],
            name=row["name"],
            permissions=list(row["permissions"] or []),
            token_ttl_seconds=row["token_ttl_seconds"],
            enabled=row["enabled"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _clamp_ttl(seconds: int) -> int:
    return max(60, min(int(seconds or _DEFAULT_TTL_SECONDS), _MAX_TTL_SECONDS))


async def create_client(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    name: str,
    permissions: list[str],
    token_ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    created_by: UUID,
) -> tuple[OAuthClient, str, str]:
    """Register a client. Returns (client, client_id, raw_secret).

    The raw secret is only available here — store the hash, show it once.
    """
    client_id = _CLIENT_ID_PREFIX + secrets.token_urlsafe(16)
    raw_secret = secrets.token_urlsafe(32)
    row = await pool.fetchrow(
        """INSERT INTO oauth_clients
               (organization_id, client_id, client_secret_hash, name,
                permissions, token_ttl_seconds, created_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING *""",
        organization_id,
        client_id,
        hash_password(raw_secret),
        name,
        permissions,
        _clamp_ttl(token_ttl_seconds),
        created_by,
    )
    return OAuthClient.from_row(row), client_id, raw_secret


async def list_clients(
    pool: asyncpg.Pool, *, organization_id: UUID
) -> list[OAuthClient]:
    rows = await pool.fetch(
        "SELECT * FROM oauth_clients WHERE organization_id = $1 ORDER BY name",
        organization_id,
    )
    return [OAuthClient.from_row(r) for r in rows]


async def set_enabled(
    pool: asyncpg.Pool, client_pk: UUID, enabled: bool, *, organization_id: UUID
) -> bool:
    result = await pool.execute(
        """UPDATE oauth_clients SET enabled = $1, updated_at = now()
           WHERE id = $2 AND organization_id = $3""",
        enabled, client_pk, organization_id,
    )
    return result == "UPDATE 1"


async def delete_client(
    pool: asyncpg.Pool, client_pk: UUID, *, organization_id: UUID
) -> bool:
    result = await pool.execute(
        "DELETE FROM oauth_clients WHERE id = $1 AND organization_id = $2",
        client_pk, organization_id,
    )
    return result == "DELETE 1"


async def issue_token(
    pool: asyncpg.Pool, client_id: str, client_secret: str
) -> tuple[str, int] | None:
    """Validate client credentials and mint a short-lived bearer token.

    Returns (raw_token, expires_in_seconds), or None when the credentials are
    invalid, the client is disabled, or its owning user is gone/inactive.
    A single None for every failure mode — the token endpoint must not leak
    which part was wrong.
    """
    row = await pool.fetchrow(
        "SELECT * FROM oauth_clients WHERE client_id = $1", client_id
    )
    if not row or not row["enabled"]:
        # Burn a hash comparison anyway so enumeration can't time-probe ids.
        verify_password(client_secret, hash_password("timing-equalizer"))
        return None
    if not verify_password(client_secret, row["client_secret_hash"]):
        return None
    if row["created_by"] is None:
        return None  # owning admin was deleted; client must be re-registered
    owner = await pool.fetchrow(
        "SELECT is_active FROM users WHERE id = $1", row["created_by"]
    )
    if not owner or not owner["is_active"]:
        return None

    ttl = _clamp_ttl(row["token_ttl_seconds"])
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    _, raw_token = await token_service.create_token(
        pool,
        row["created_by"],
        name=f"oauth:{row['name']}"[:100],
        permissions=list(row["permissions"] or []),
        expires_at=expires_at,
        is_service_account=True,
    )
    return raw_token, ttl
