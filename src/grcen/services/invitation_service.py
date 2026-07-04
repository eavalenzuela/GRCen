"""Self-service org invitations.

An admin mints a single-use, expiring invite link for a role in their org;
the invitee sets a username and password on a public accept page and lands in
the right organization — no admin-typed passwords, no out-of-band credential
handoff. The raw token only ever exists in the link (SHA-256 hash at rest,
same discipline as API tokens), invitations expire (default 7 days), and can
be revoked before acceptance.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.permissions import UserRole
from grcen.services.auth import create_user

DEFAULT_TTL_DAYS = 7
_MIN_PASSWORD_LEN = 8


class InvitationError(ValueError):
    """User-facing failure accepting an invitation."""


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def invitation_status(row) -> str:
    """pending / accepted / revoked / expired for a raw invitation row."""
    if row["accepted_at"]:
        return "accepted"
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"] < datetime.now(UTC):
        return "expired"
    return "pending"


async def create_invitation(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    role: UserRole,
    email: str = "",
    note: str = "",
    created_by: UUID,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> tuple[dict, str]:
    """Mint an invitation. Returns (row, raw_token) — the token shows once."""
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=max(1, ttl_days))
    row = await pool.fetchrow(
        """INSERT INTO org_invitations
               (organization_id, token_hash, role, email, note, created_by, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING *""",
        organization_id,
        _hash_token(raw),
        role.value,
        email[:255],
        note[:255],
        created_by,
        expires,
    )
    return dict(row), raw


async def list_invitations(
    pool: asyncpg.Pool, *, organization_id: UUID
) -> list[dict]:
    rows = await pool.fetch(
        """SELECT i.*, u.username AS created_by_username,
                  a.username AS accepted_by_username
           FROM org_invitations i
           LEFT JOIN users u ON u.id = i.created_by
           LEFT JOIN users a ON a.id = i.accepted_by
           WHERE i.organization_id = $1
           ORDER BY i.created_at DESC""",
        organization_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["status"] = invitation_status(r)
        out.append(d)
    return out


async def revoke_invitation(
    pool: asyncpg.Pool, invitation_id: UUID, *, organization_id: UUID
) -> bool:
    result = await pool.execute(
        """UPDATE org_invitations SET revoked_at = now()
           WHERE id = $1 AND organization_id = $2
             AND accepted_at IS NULL AND revoked_at IS NULL""",
        invitation_id, organization_id,
    )
    return result == "UPDATE 1"


async def get_valid_by_token(pool: asyncpg.Pool, raw_token: str):
    """The pending, unexpired, unrevoked invitation for a raw token, or None."""
    if not raw_token:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM org_invitations WHERE token_hash = $1",
        _hash_token(raw_token),
    )
    if row is None or invitation_status(row) != "pending":
        return None
    return row


async def accept_invitation(
    pool: asyncpg.Pool, raw_token: str, *, username: str, password: str
):
    """Create the invited user and consume the invitation.

    Raises InvitationError with a user-facing message on any failure.
    Returns the created User.
    """
    invitation = await get_valid_by_token(pool, raw_token)
    if invitation is None:
        raise InvitationError("This invitation is no longer valid.")
    username = username.strip()
    if not username:
        raise InvitationError("Username is required.")
    if len(password) < _MIN_PASSWORD_LEN:
        raise InvitationError(
            f"Password must be at least {_MIN_PASSWORD_LEN} characters."
        )
    # create_user upserts on username — an existing account must never be
    # overwritten by an invitee, so check availability explicitly first.
    existing = await pool.fetchrow(
        "SELECT 1 FROM users WHERE username = $1", username
    )
    if existing:
        raise InvitationError("That username is already taken.")

    user = await create_user(
        pool,
        username,
        password,
        role=UserRole(invitation["role"]),
        organization_id=invitation["organization_id"],
    )
    consumed = await pool.execute(
        """UPDATE org_invitations SET accepted_at = now(), accepted_by = $1
           WHERE id = $2 AND accepted_at IS NULL AND revoked_at IS NULL""",
        user.id, invitation["id"],
    )
    if consumed != "UPDATE 1":
        # Lost a race with a concurrent accept/revoke — roll the account back.
        await pool.execute("DELETE FROM users WHERE id = $1", user.id)
        raise InvitationError("This invitation is no longer valid.")
    return user
