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

    client_ip = request.client.host if request.client else None
    token = await validate_token(pool, raw_token, client_ip=client_ip)
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
    """Resolve the request's authenticated user, with an active-org overlay.

    The user's "current" org comes from one of:
      1. A token's owning org (locked at token creation — no switching).
      2. ``request.session['active_org_id']`` — but only if the user is still a
         member of that org. A stale id falls back to the user's default org.

    The override gets applied to ``user.organization_id`` so the rest of the
    code keeps reading from a single place. The user's per-org role gets
    swapped in too, so a viewer-in-org-B doesn't keep their admin powers from
    org-A after switching.
    """
    user_id: str | None = None
    via_token = False

    bearer = await _resolve_bearer_token(request, pool)
    if bearer is not None:
        user_id, token_permissions = bearer
        request.state.token_permissions = token_permissions
        via_token = True
    else:
        user_id = await _get_user_id_from_session(request, pool)

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_by_id(pool, UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Tokens are bound to their issuing org — never switch them.
    if via_token:
        return user

    active_id = request.session.get("active_org_id")
    if active_id:
        from grcen.services import organization_service
        try:
            active_uuid = UUID(active_id)
        except (ValueError, TypeError):
            active_uuid = None
        if active_uuid:
            is_member, role_in_org = await organization_service.is_member(
                pool, user.id, active_uuid
            )
            if is_member:
                from grcen.permissions import UserRole
                user.organization_id = active_uuid
                if role_in_org:
                    try:
                        user.role = UserRole(role_in_org)
                    except ValueError:
                        pass
            else:
                # Stale active org — clear it.
                request.session.pop("active_org_id", None)
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
    """Return a FastAPI dependency that enforces the given permissions.

    Superadmin users implicitly hold every permission, including the
    org-management permissions that no per-org role grants. Token-based callers
    still have to declare each permission on the token, which keeps a stolen
    token to its declared scope even if the underlying user is a superadmin.
    """

    async def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        for perm in permissions:
            if not (user.is_superadmin or has_permission(user.role, perm)):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            token_perms = getattr(request.state, "token_permissions", None)
            if token_perms is not None and perm.value not in token_perms:
                raise HTTPException(status_code=403, detail="Token lacks required permission")
        return user

    return dependency
