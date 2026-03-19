from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_db
from grcen.schemas.asset import (
    AssetCreate,
    AssetListResponse,
    AssetResponse,
    AssetUpdate,
)
from grcen.services import asset as asset_svc

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("/", response_model=AssetListResponse)
async def list_assets(
    type: AssetType | None = None,
    page: int = 1,
    page_size: int = 25,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    items, total = await asset_svc.list_assets(
        pool, asset_type=type, page=page, page_size=page_size
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
    _user: User = Depends(get_current_user),
):
    results = await asset_svc.search_assets(pool, q)
    return [AssetResponse.model_validate(a, from_attributes=True) for a in results]


@router.post("/", response_model=AssetResponse, status_code=201)
async def create_asset(
    data: AssetCreate,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    asset = await asset_svc.create_asset(
        pool,
        type=data.type,
        name=data.name,
        description=data.description,
        status=data.status.value,
        owner=data.owner,
        metadata_=data.metadata_,
    )
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(get_current_user),
):
    kwargs = data.model_dump(exclude_unset=True)
    if "status" in kwargs and kwargs["status"]:
        kwargs["status"] = kwargs["status"].value
    asset = await asset_svc.update_asset(pool, asset_id, **kwargs)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    deleted = await asset_svc.delete_asset(pool, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset not found")
