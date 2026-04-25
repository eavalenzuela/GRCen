"""Server-side session management backed by PostgreSQL."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.config import settings
from grcen.services import encryption_config
from grcen.services.encryption import encrypt_field


_ROLE_CAP_OVERRIDES = {
    "admin": "SESSION_MAX_CONCURRENT_ADMIN",
    "auditor": "SESSION_MAX_CONCURRENT_AUDITOR",
    "editor": "SESSION_MAX_CONCURRENT_EDITOR",
    "viewer": "SESSION_MAX_CONCURRENT_VIEWER",
}


def _cap_for_role(role: str | None) -> int:
    """Per-role override takes priority; -1 falls through to the global default."""
    if role and role in _ROLE_CAP_OVERRIDES:
        override = getattr(settings, _ROLE_CAP_OVERRIDES[role], -1)
        if override is not None and override >= 0:
            return override
    return settings.SESSION_MAX_CONCURRENT


async def _enforce_concurrent_cap(
    pool: asyncpg.Pool, user_id: UUID
) -> list[str]:
    """Evict the user's oldest sessions when above the cap.

    Runs *before* the new session is inserted. Returns the list of evicted
    session ids so callers can post a notification to the user about the
    sign-out.
    """
    role_row = await pool.fetchrow("SELECT role FROM users WHERE id = $1", user_id)
    cap = _cap_for_role(role_row["role"] if role_row else None)
    if cap <= 0:
        return []
    # Keep cap-1 of the most recently active sessions so the new one fills the
    # last slot. last_active is the right ordering key — a brand-new login
    # should bump out a long-idle one, not a freshly active parallel session.
    rows = await pool.fetch(
        """DELETE FROM sessions
           WHERE session_id IN (
               SELECT session_id FROM sessions
               WHERE user_id = $1
               ORDER BY last_active DESC
               OFFSET $2
           )
           RETURNING session_id""",
        user_id,
        cap - 1,
    )
    evicted = [r["session_id"] for r in rows]
    if evicted:
        # Drop a notification so the user sees this on their next page load.
        await _record_eviction_notice(pool, user_id, len(evicted))
    return evicted


async def _record_eviction_notice(
    pool: asyncpg.Pool, user_id: UUID, count: int
) -> None:
    """Insert an in-app notification (no alert_id — this is a system message)."""
    org_row = await pool.fetchrow(
        "SELECT organization_id FROM users WHERE id = $1", user_id
    )
    if not org_row:
        return
    import uuid as _uuid
    await pool.execute(
        """INSERT INTO notifications
               (id, alert_id, user_id, title, message, organization_id)
           VALUES ($1, NULL, $2, $3, $4, $5)""",
        _uuid.uuid4(),
        user_id,
        f"Session{'s' if count != 1 else ''} signed out",
        (
            f"{count} of your older session{'s were' if count != 1 else ' was'} "
            "signed out because you're at the concurrent-session cap."
        ),
        org_row["organization_id"],
    )


async def create_session(
    pool: asyncpg.Pool,
    user_id: UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a new server-side session and return the session ID."""
    await _enforce_concurrent_cap(pool, user_id)
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


async def list_sessions_for_user(pool: asyncpg.Pool, user_id: UUID) -> list[dict]:
    """Return active sessions for a user, newest-active first."""
    rows = await pool.fetch(
        """SELECT session_id, created_at, last_active, ip_address, user_agent
           FROM sessions WHERE user_id = $1
           ORDER BY last_active DESC""",
        user_id,
    )
    return [dict(r) for r in rows]


async def list_all_sessions(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict]:
    """Cross-user session listing for the admin page."""
    where = []
    vals = []
    idx = 1
    if organization_id is not None:
        where.append(f"u.organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await pool.fetch(
        f"""SELECT s.session_id, s.user_id, u.username, s.created_at,
                   s.last_active, s.ip_address, s.user_agent
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            {where_sql}
            ORDER BY s.last_active DESC""",
        *vals,
    )
    return [dict(r) for r in rows]


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
