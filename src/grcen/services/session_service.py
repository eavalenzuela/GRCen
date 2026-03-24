"""Server-side session management backed by PostgreSQL."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.services import encryption_config
from grcen.services.encryption import encrypt_field


async def create_session(
    pool: asyncpg.Pool,
    user_id: UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a new server-side session and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    stored_ip = ip_address
    if ip_address and await encryption_config.is_scope_active(pool, "session_pii"):
        stored_ip = encrypt_field(ip_address, "session_pii")
    await pool.execute(
        """INSERT INTO sessions (session_id, user_id, ip_address, user_agent)
           VALUES ($1, $2, $3, $4)""",
        session_id,
        user_id,
        stored_ip,
        (user_agent or "")[:512],  # truncate long user-agent strings
    )
    return session_id


async def validate_session(
    pool: asyncpg.Pool,
    session_id: str,
    idle_timeout_minutes: int,
    absolute_timeout_minutes: int,
) -> UUID | None:
    """Validate a session and return the user_id, or None if expired/invalid.

    Updates last_active on valid sessions.
    """
    row = await pool.fetchrow(
        "SELECT user_id, created_at, last_active FROM sessions WHERE session_id = $1",
        session_id,
    )
    if not row:
        return None

    now = datetime.now(UTC)
    created = row["created_at"]
    last_active = row["last_active"]

    # Ensure timezone-aware comparison
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=UTC)

    # Absolute timeout
    if now - created > timedelta(minutes=absolute_timeout_minutes):
        await pool.execute("DELETE FROM sessions WHERE session_id = $1", session_id)
        return None

    # Idle timeout
    if now - last_active > timedelta(minutes=idle_timeout_minutes):
        await pool.execute("DELETE FROM sessions WHERE session_id = $1", session_id)
        return None

    # Touch last_active
    await pool.execute(
        "UPDATE sessions SET last_active = $1 WHERE session_id = $2",
        now,
        session_id,
    )
    return row["user_id"]


async def invalidate_session(pool: asyncpg.Pool, session_id: str) -> None:
    """Delete a specific session."""
    await pool.execute("DELETE FROM sessions WHERE session_id = $1", session_id)


async def invalidate_user_sessions(pool: asyncpg.Pool, user_id: UUID) -> None:
    """Delete all sessions for a user (e.g. on password change or deactivation)."""
    await pool.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
