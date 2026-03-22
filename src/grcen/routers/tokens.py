from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.permissions import Permission, ROLE_PERMISSIONS, has_permission
from grcen.routers.deps import get_current_user, get_db, require_permission
from grcen.schemas.api_token import (
    TokenConfigResponse,
    TokenConfigUpdate,
    TokenCreate,
    TokenCreatedResponse,
    TokenResponse,
)
from grcen.services import audit_service as audit_svc
from grcen.services import token_service

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


def _validate_permissions(requested: list[str], user: User) -> list[str]:
    """Validate that requested permissions are real and within the user's role."""
    role_perms = ROLE_PERMISSIONS.get(user.role, set())
    valid_values = {p.value for p in Permission}
    checked: list[str] = []
    for p in requested:
        if p not in valid_values:
            raise HTTPException(status_code=400, detail=f"Unknown permission: {p}")
        perm = Permission(p)
        if perm not in role_perms:
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{p}' exceeds your role's capabilities",
            )
        checked.append(p)
    if not checked:
        raise HTTPException(status_code=400, detail="At least one permission is required")
    return checked


# --- Self-service endpoints ---


@router.post("/", response_model=TokenCreatedResponse, status_code=201)
async def create_token(
    data: TokenCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    perms = _validate_permissions(data.permissions, user)

    if data.is_service_account and not has_permission(user.role, Permission.MANAGE_USERS):
        raise HTTPException(
            status_code=403,
            detail="Only admins can create service account tokens",
        )

    token, raw = await token_service.create_token(
        pool,
        user_id=user.id,
        name=data.name,
        permissions=perms,
        expires_at=data.expires_at,
        is_service_account=data.is_service_account,
    )

    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="api_token",
        entity_id=token.id,
        entity_name=token.name,
    )

    base = TokenResponse.model_validate(token, from_attributes=True)
    return TokenCreatedResponse(**base.model_dump(), token=raw)


@router.get("/", response_model=list[TokenResponse])
async def list_my_tokens(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tokens = await token_service.list_tokens_for_user(pool, user.id)
    return [TokenResponse.model_validate(t, from_attributes=True) for t in tokens]


@router.delete("/{token_id}", status_code=204)
async def revoke_my_token(
    token_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    token = await token_service.get_token_by_id(pool, token_id)
    if not token or token.user_id != user.id:
        raise HTTPException(status_code=404, detail="Token not found")
    if not await token_service.revoke_token(pool, token_id):
        raise HTTPException(status_code=400, detail="Token already revoked")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="revoke",
        entity_type="api_token",
        entity_id=token_id,
        entity_name=token.name,
    )


# --- Admin endpoints ---


@router.get("/config", response_model=TokenConfigResponse)
async def get_token_config(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    max_days = await token_service.get_max_expiry_days(pool)
    return TokenConfigResponse(max_expiry_days=max_days)


@router.put("/config", response_model=TokenConfigResponse)
async def update_token_config(
    data: TokenConfigUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    await token_service.set_max_expiry_days(pool, data.max_expiry_days)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="update",
        entity_type="token_config",
        entity_id=None,
        entity_name="token_max_expiry_days",
        changes={"max_expiry_days": data.max_expiry_days},
    )
    return TokenConfigResponse(max_expiry_days=data.max_expiry_days)


@router.get("/all", response_model=list[TokenResponse])
async def list_all_tokens(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    tokens = await token_service.list_all_tokens(pool)
    return [TokenResponse.model_validate(t, from_attributes=True) for t in tokens]


@router.delete("/all/{token_id}", status_code=204)
async def admin_revoke_token(
    token_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    token = await token_service.get_token_by_id(pool, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if not await token_service.revoke_token(pool, token_id):
        raise HTTPException(status_code=400, detail="Token already revoked")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="revoke",
        entity_type="api_token",
        entity_id=token_id,
        entity_name=token.name,
    )
