from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request

from grcen.database import get_pool
from grcen.models.user import User
from grcen.permissions import Permission, has_permission
from grcen.services.auth import get_user_by_id


async def get_db(pool: asyncpg.Pool = Depends(get_pool)) -> asyncpg.Pool:
    return pool


def _get_user_id_from_request(request: Request) -> str | None:
    """Extract user identity from the request.

    Currently reads from the session cookie.  Future auth methods
    (e.g. OIDC bearer token) can be added here as additional checks.
    """
    return request.session.get("user_id")


async def get_current_user(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User:
    user_id = _get_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await get_user_by_id(pool, UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_current_user_or_none(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User | None:
    user_id = _get_user_id_from_request(request)
    if not user_id:
        return None
    return await get_user_by_id(pool, UUID(user_id))


def require_permission(*permissions: Permission):
    """Return a FastAPI dependency that enforces the given permissions."""

    async def dependency(user: User = Depends(get_current_user)) -> User:
        for perm in permissions:
            if not has_permission(user.role, perm):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency
