import uuid
from uuid import UUID

import asyncpg
from passlib.context import CryptContext

from grcen.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


async def create_user(
    pool: asyncpg.Pool, username: str, password: str, is_admin: bool = False
) -> User:
    row = await pool.fetchrow(
        """
        INSERT INTO users (id, username, hashed_password, is_active, is_admin)
        VALUES ($1, $2, $3, true, $4)
        RETURNING *
        """,
        uuid.uuid4(),
        username,
        hash_password(password),
        is_admin,
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
