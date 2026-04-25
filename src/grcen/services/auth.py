import uuid
from uuid import UUID

import asyncpg
import bcrypt

from grcen.models.user import User
from grcen.permissions import UserRole
from grcen.services import encryption_config
from grcen.services.encryption import blind_index, decrypt_field, encrypt_field

# Sentinel value for users without a local password (e.g. future OIDC/SSO users).
_UNUSABLE_PASSWORD = "!unusable"

_SCOPE = "user_pii"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if hashed == _UNUSABLE_PASSWORD:
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── encryption helpers ────────────────────────────────────────────────────


async def _pii_active(pool: asyncpg.Pool) -> bool:
    return await encryption_config.is_scope_active(pool, _SCOPE)


def _decrypt_user_email(user: User) -> User:
    """Decrypt the email field on a User object (no-op if plaintext)."""
    if user.email:
        user.email = decrypt_field(user.email, _SCOPE)
    return user


def _decrypt_users_email(users: list[User]) -> list[User]:
    for u in users:
        if u.email:
            u.email = decrypt_field(u.email, _SCOPE)
    return users


async def _prepare_email(pool: asyncpg.Pool, email: str | None) -> tuple[str | None, str | None]:
    """Return (stored_email, blind_idx) — encrypting if the scope is active."""
    if not email:
        return email, None
    if await _pii_active(pool):
        return encrypt_field(email, _SCOPE), blind_index(email)
    return email, None


# ── public API ────────────────────────────────────────────────────────────


async def create_user(
    pool: asyncpg.Pool,
    username: str,
    password: str | None = None,
    role: UserRole = UserRole.VIEWER,
    organization_id: UUID | None = None,
) -> User:
    from grcen.services import organization_service
    if organization_id is None:
        organization_id = await organization_service.get_default_org_id(pool)
    hashed = hash_password(password) if password else _UNUSABLE_PASSWORD
    row = await pool.fetchrow(
        """
        INSERT INTO users (id, username, hashed_password, is_active, is_admin, role, organization_id)
        VALUES ($1, $2, $3, true, $4, $5, $6)
        ON CONFLICT (username) DO UPDATE
            SET hashed_password = EXCLUDED.hashed_password,
                is_admin = EXCLUDED.is_admin,
                role = EXCLUDED.role,
                updated_at = now()
        RETURNING *
        """,
        uuid.uuid4(),
        username,
        hashed,
        role == UserRole.ADMIN,
        role.value,
        organization_id,
    )
    return _decrypt_user_email(User.from_row(row))


async def check_lockout(pool: asyncpg.Pool, username: str) -> bool:
    """Return True if the user account is currently locked out."""
    from datetime import UTC, datetime
    row = await pool.fetchrow(
        "SELECT locked_until FROM users WHERE username = $1", username
    )
    if not row or not row["locked_until"]:
        return False
    locked = row["locked_until"]
    if locked.tzinfo is None:
        locked = locked.replace(tzinfo=UTC)
    return datetime.now(UTC) < locked


async def record_failed_login(
    pool: asyncpg.Pool, username: str, max_attempts: int, lockout_minutes: int
) -> None:
    """Increment failed login count and lock the account if threshold is reached."""
    from datetime import UTC, datetime, timedelta
    await pool.execute(
        """UPDATE users
           SET failed_login_count = failed_login_count + 1,
               locked_until = CASE
                   WHEN failed_login_count + 1 >= $2
                   THEN $3
                   ELSE locked_until
               END,
               updated_at = now()
           WHERE username = $1""",
        username,
        max_attempts,
        datetime.now(UTC) + timedelta(minutes=lockout_minutes),
    )


async def record_successful_login(pool: asyncpg.Pool, user_id: UUID) -> None:
    """Reset failed login counters and set last_login timestamp."""
    from datetime import UTC, datetime
    await pool.execute(
        """UPDATE users
           SET failed_login_count = 0, locked_until = NULL,
               last_login = $1, updated_at = now()
           WHERE id = $2""",
        datetime.now(UTC),
        user_id,
    )


async def authenticate_user(
    pool: asyncpg.Pool, username: str, password: str
) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE username = $1", username)
    if not row:
        return None
    user = _decrypt_user_email(User.from_row(row))
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_user_by_id(pool: asyncpg.Pool, user_id: UUID) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return _decrypt_user_email(User.from_row(row)) if row else None


async def list_users(
    pool: asyncpg.Pool, organization_id: UUID | None = None
) -> list[User]:
    if organization_id is not None:
        rows = await pool.fetch(
            "SELECT * FROM users WHERE organization_id = $1 ORDER BY username",
            organization_id,
        )
    else:
        rows = await pool.fetch("SELECT * FROM users ORDER BY username")
    users = [User.from_row(r) for r in rows]
    return _decrypt_users_email(users)


async def update_user_role(pool: asyncpg.Pool, user_id: UUID, role: UserRole) -> User | None:
    row = await pool.fetchrow(
        """UPDATE users SET role = $1, is_admin = $2, updated_at = now()
           WHERE id = $3 RETURNING *""",
        role.value,
        role == UserRole.ADMIN,
        user_id,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None


async def set_user_active(pool: asyncpg.Pool, user_id: UUID, active: bool) -> User | None:
    row = await pool.fetchrow(
        "UPDATE users SET is_active = $1, updated_at = now() WHERE id = $2 RETURNING *",
        active,
        user_id,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None


async def set_email_notifications_enabled(
    pool: asyncpg.Pool, user_id: UUID, enabled: bool
) -> User | None:
    row = await pool.fetchrow(
        """UPDATE users SET email_notifications_enabled = $1, updated_at = now()
           WHERE id = $2 RETURNING *""",
        enabled,
        user_id,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None


async def delete_user(pool: asyncpg.Pool, user_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM users WHERE id = $1", user_id)
    return result == "DELETE 1"


async def get_user_by_oidc_sub(pool: asyncpg.Pool, oidc_sub: str) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE oidc_sub = $1", oidc_sub)
    return _decrypt_user_email(User.from_row(row)) if row else None


async def get_user_by_email(pool: asyncpg.Pool, email: str) -> User | None:
    if await _pii_active(pool):
        idx = blind_index(email)
        row = await pool.fetchrow(
            "SELECT * FROM users WHERE email_blind_idx = $1", idx
        )
    else:
        row = await pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
    return _decrypt_user_email(User.from_row(row)) if row else None


async def create_oidc_user(
    pool: asyncpg.Pool,
    username: str,
    email: str | None,
    oidc_sub: str,
    role: UserRole = UserRole.VIEWER,
    organization_id: UUID | None = None,
) -> User:
    from grcen.services import organization_service
    if organization_id is None:
        organization_id = await organization_service.get_default_org_id(pool)
    stored_email, email_idx = await _prepare_email(pool, email)
    row = await pool.fetchrow(
        """INSERT INTO users
               (id, username, email, email_blind_idx, hashed_password,
                is_active, is_admin, role, oidc_sub, organization_id)
           VALUES ($1, $2, $3, $4, $5, true, $6, $7, $8, $9)
           RETURNING *""",
        uuid.uuid4(),
        username,
        stored_email,
        email_idx,
        _UNUSABLE_PASSWORD,
        role == UserRole.ADMIN,
        role.value,
        oidc_sub,
        organization_id,
    )
    return _decrypt_user_email(User.from_row(row))


async def update_oidc_user(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    email: str | None = None,
    role: UserRole | None = None,
    oidc_sub: str | None = None,
) -> User | None:
    sets = ["updated_at = now()"]
    params: list = []
    idx = 1
    if email is not None:
        stored_email, email_idx = await _prepare_email(pool, email)
        sets.append(f"email = ${idx}")
        params.append(stored_email)
        idx += 1
        sets.append(f"email_blind_idx = ${idx}")
        params.append(email_idx)
        idx += 1
    if role is not None:
        sets.append(f"role = ${idx}")
        params.append(role.value)
        idx += 1
        sets.append(f"is_admin = ${idx}")
        params.append(role == UserRole.ADMIN)
        idx += 1
    if oidc_sub is not None:
        sets.append(f"oidc_sub = ${idx}")
        params.append(oidc_sub)
        idx += 1
    params.append(user_id)
    row = await pool.fetchrow(
        f"UPDATE users SET {', '.join(sets)} WHERE id = ${idx} RETURNING *",
        *params,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None


async def get_user_by_saml_sub(pool: asyncpg.Pool, saml_sub: str) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE saml_sub = $1", saml_sub)
    return _decrypt_user_email(User.from_row(row)) if row else None


async def create_saml_user(
    pool: asyncpg.Pool,
    username: str,
    email: str | None,
    saml_sub: str,
    role: UserRole = UserRole.VIEWER,
    organization_id: UUID | None = None,
) -> User:
    from grcen.services import organization_service
    if organization_id is None:
        organization_id = await organization_service.get_default_org_id(pool)
    stored_email, email_idx = await _prepare_email(pool, email)
    row = await pool.fetchrow(
        """INSERT INTO users
               (id, username, email, email_blind_idx, hashed_password,
                is_active, is_admin, role, saml_sub, organization_id)
           VALUES ($1, $2, $3, $4, $5, true, $6, $7, $8, $9)
           RETURNING *""",
        uuid.uuid4(),
        username,
        stored_email,
        email_idx,
        _UNUSABLE_PASSWORD,
        role == UserRole.ADMIN,
        role.value,
        saml_sub,
        organization_id,
    )
    return _decrypt_user_email(User.from_row(row))


async def update_saml_user(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    email: str | None = None,
    role: UserRole | None = None,
    saml_sub: str | None = None,
) -> User | None:
    sets = ["updated_at = now()"]
    params: list = []
    idx = 1
    if email is not None:
        stored_email, email_idx = await _prepare_email(pool, email)
        sets.append(f"email = ${idx}")
        params.append(stored_email)
        idx += 1
        sets.append(f"email_blind_idx = ${idx}")
        params.append(email_idx)
        idx += 1
    if role is not None:
        sets.append(f"role = ${idx}")
        params.append(role.value)
        idx += 1
        sets.append(f"is_admin = ${idx}")
        params.append(role == UserRole.ADMIN)
        idx += 1
    if saml_sub is not None:
        sets.append(f"saml_sub = ${idx}")
        params.append(saml_sub)
        idx += 1
    params.append(user_id)
    row = await pool.fetchrow(
        f"UPDATE users SET {', '.join(sets)} WHERE id = ${idx} RETURNING *",
        *params,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None


async def set_person_asset_link(
    pool: asyncpg.Pool, user_id: UUID, person_asset_id: UUID | None
) -> User | None:
    row = await pool.fetchrow(
        "UPDATE users SET person_asset_id = $1, updated_at = now() WHERE id = $2 RETURNING *",
        person_asset_id,
        user_id,
    )
    return _decrypt_user_email(User.from_row(row)) if row else None
