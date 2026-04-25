from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request

from grcen.config import settings
from grcen.database import get_pool
from grcen.models.user import User
from grcen.permissions import Permission, has_permission
from grcen.services.auth import get_user_by_id
from grcen.services import session_service


async def get_db(pool: asyncpg.Pool = Depends(get_pool)) -> asyncpg.Pool:
    return pool


async def _resolve_bearer_token(request: Request, pool: asyncpg.Pool) -> tuple[str, list[str]] | None:
    """If an Authorization: Bearer header is present, validate it and return (user_id, permissions)."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    raw_token = auth_header[7:]
    if not raw_token:
        return None

    from grcen.services.token_service import validate_token

    token = await validate_token(pool, raw_token)
    if token is None:
        return None

    return str(token.user_id), token.permissions


async def _get_user_id_from_session(request: Request, pool: asyncpg.Pool) -> str | None:
    """Validate the server-side session and return the user_id, or None."""
    session_id = request.session.get("session_id")
    if not session_id:
        return None

    user_id = await session_service.validate_session(
        pool,
        session_id,
        idle_timeout_minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES,
        absolute_timeout_minutes=settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES,
    )
    if user_id is None:
        # Session expired — clear the cookie
        request.session.clear()
        return None
    return str(user_id)


async def get_current_user(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User:
    user_id: str | None = None

    # Try Bearer token first
    bearer = await _resolve_bearer_token(request, pool)
    if bearer is not None:
        user_id, token_permissions = bearer
        request.state.token_permissions = token_permissions
    else:
        user_id = await _get_user_id_from_session(request, pool)

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_by_id(pool, UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_current_user_or_none(
    request: Request, pool: asyncpg.Pool = Depends(get_db)
) -> User | None:
    user_id: str | None = None

    bearer = await _resolve_bearer_token(request, pool)
    if bearer is not None:
        user_id, token_permissions = bearer
        request.state.token_permissions = token_permissions
    else:
        user_id = await _get_user_id_from_session(request, pool)

    if not user_id:
        return None
    return await get_user_by_id(pool, UUID(user_id))


async def get_current_organization_id(
    user: User = Depends(get_current_user),
) -> UUID:
    """Tenant scope for the current request.

    Every read or write that touches per-tenant data must be scoped through
    this — never trust an `organization_id` arriving from the client. Multi-org
    membership is not yet modeled, so the user's single org is authoritative.
    """
    return user.organization_id


def require_permission(*permissions: Permission):
    """Return a FastAPI dependency that enforces the given permissions."""

    async def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        for perm in permissions:
            # User's role must grant the permission
            if not has_permission(user.role, perm):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            # If authenticating via API token, the token must also include the permission
            token_perms = getattr(request.state, "token_permissions", None)
            if token_perms is not None and perm.value not in token_perms:
                raise HTTPException(status_code=403, detail="Token lacks required permission")
        return user

    return dependency
