from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.asset import (
    AssetCreate,
    AssetListResponse,
    AssetResponse,
    AssetUpdate,
)
from grcen.services import asset as asset_svc
from grcen.services import audit_service as audit_svc

router = APIRouter(prefix="/api/assets", tags=["assets"])

_ASSET_FIELDS = ["name", "description", "status", "owner", "metadata"]


@router.get("/", response_model=AssetListResponse)
async def list_assets(
    type: AssetType | None = None,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    page: int = 1,
    page_size: int = 25,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    items, total = await asset_svc.list_assets(
        pool, asset_type=type, page=page, page_size=page_size,
        q=q, status=status, owner=owner,
        created_after=created_after, created_before=created_before,
    )
    return AssetListResponse(
        items=[AssetResponse.model_validate(a, from_attributes=True) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search")
async def search_assets(
    q: str = "",
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    results = await asset_svc.search_assets(pool, q)
    return [AssetResponse.model_validate(a, from_attributes=True) for a in results]


@router.post("/", response_model=AssetResponse, status_code=201)
async def create_asset(
    data: AssetCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    asset = await asset_svc.create_asset(
        pool,
        type=data.type,
        name=data.name,
        description=data.description,
        status=data.status.value,
        owner=data.owner,
        metadata_=data.metadata_,
        updated_by=user.id,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="asset",
        entity_id=asset.id,
        entity_name=asset.name,
        changes=audit_svc.create_snapshot(asset.__dict__, _ASSET_FIELDS),
    )
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    asset = await asset_svc.get_asset(pool, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.put("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: UUID,
    data: AssetUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    old = await asset_svc.get_asset(pool, asset_id)
    if not old:
        raise HTTPException(status_code=404, detail="Asset not found")
    kwargs = data.model_dump(exclude_unset=True)
    if "status" in kwargs and kwargs["status"]:
        kwargs["status"] = kwargs["status"].value
    asset = await asset_svc.update_asset(pool, asset_id, **kwargs, updated_by=user.id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    diff = audit_svc.compute_diff(old.__dict__, asset.__dict__, _ASSET_FIELDS)
    if diff:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="update",
            entity_type="asset",
            entity_id=asset.id,
            entity_name=asset.name,
            changes=diff,
        )
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await asset_svc.get_asset(pool, asset_id)
    if not old:
        raise HTTPException(status_code=404, detail="Asset not found")
    deleted = await asset_svc.delete_asset(pool, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset not found")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="asset",
        entity_id=old.id,
        entity_name=old.name,
        changes=audit_svc.delete_snapshot(old.__dict__, _ASSET_FIELDS),
    )
