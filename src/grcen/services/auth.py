import uuid
from uuid import UUID

import asyncpg
import bcrypt

from grcen.models.user import User
from grcen.permissions import UserRole

# Sentinel value for users without a local password (e.g. future OIDC/SSO users).
_UNUSABLE_PASSWORD = "!unusable"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if hashed == _UNUSABLE_PASSWORD:
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def create_user(
    pool: asyncpg.Pool,
    username: str,
    password: str | None = None,
    role: UserRole = UserRole.VIEWER,
) -> User:
    hashed = hash_password(password) if password else _UNUSABLE_PASSWORD
    row = await pool.fetchrow(
        """
        INSERT INTO users (id, username, hashed_password, is_active, is_admin, role)
        VALUES ($1, $2, $3, true, $4, $5)
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
    )
    return User.from_row(row)


async def authenticate_user(
    pool: asyncpg.Pool, username: str, password: str
) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE username = $1", username)
    if not row:
        return None
    user = User.from_row(row)
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_user_by_id(pool: asyncpg.Pool, user_id: UUID) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return User.from_row(row) if row else None


async def list_users(pool: asyncpg.Pool) -> list[User]:
    rows = await pool.fetch("SELECT * FROM users ORDER BY username")
    return [User.from_row(r) for r in rows]


async def update_user_role(pool: asyncpg.Pool, user_id: UUID, role: UserRole) -> User | None:
    row = await pool.fetchrow(
        """UPDATE users SET role = $1, is_admin = $2, updated_at = now()
           WHERE id = $3 RETURNING *""",
        role.value,
        role == UserRole.ADMIN,
        user_id,
    )
    return User.from_row(row) if row else None


async def set_user_active(pool: asyncpg.Pool, user_id: UUID, active: bool) -> User | None:
    row = await pool.fetchrow(
        "UPDATE users SET is_active = $1, updated_at = now() WHERE id = $2 RETURNING *",
        active,
        user_id,
    )
    return User.from_row(row) if row else None


async def delete_user(pool: asyncpg.Pool, user_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM users WHERE id = $1", user_id)
    return result == "DELETE 1"


async def get_user_by_oidc_sub(pool: asyncpg.Pool, oidc_sub: str) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE oidc_sub = $1", oidc_sub)
    return User.from_row(row) if row else None


async def get_user_by_email(pool: asyncpg.Pool, email: str) -> User | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
    return User.from_row(row) if row else None


async def create_oidc_user(
    pool: asyncpg.Pool,
    username: str,
    email: str | None,
    oidc_sub: str,
    role: UserRole = UserRole.VIEWER,
) -> User:
    row = await pool.fetchrow(
        """INSERT INTO users (id, username, email, hashed_password, is_active, is_admin, role, oidc_sub)
           VALUES ($1, $2, $3, $4, true, $5, $6, $7)
           RETURNING *""",
        uuid.uuid4(),
        username,
        email,
        _UNUSABLE_PASSWORD,
        role == UserRole.ADMIN,
        role.value,
        oidc_sub,
    )
    return User.from_row(row)


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
        sets.append(f"email = ${idx}")
        params.append(email)
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
    return User.from_row(row) if row else None


async def set_person_asset_link(
    pool: asyncpg.Pool, user_id: UUID, person_asset_id: UUID | None
) -> User | None:
    row = await pool.fetchrow(
        "UPDATE users SET person_asset_id = $1, updated_at = now() WHERE id = $2 RETURNING *",
        person_asset_id,
        user_id,
    )
    return User.from_row(row) if row else None
