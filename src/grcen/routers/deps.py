from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request

from grcen.database import get_pool
from grcen.models.user import User
from grcen.services.auth import get_user_by_id


async def get_db(pool: asyncpg.Pool = Depends(get_pool)) -> asyncpg.Pool:
    return pool


async def get_current_user(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await get_user_by_id(pool, UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_current_user_or_none(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return await get_user_by_id(pool, UUID(user_id))
