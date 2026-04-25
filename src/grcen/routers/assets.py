from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

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
from grcen.services import redaction
from grcen.services import workflow_service

router = APIRouter(prefix="/api/assets", tags=["assets"])

_ASSET_FIELDS = ["name", "description", "status", "owner", "metadata"]


@router.get(
    "/",
    response_model=AssetListResponse,
    summary="List assets with filtering and pagination",
)
async def list_assets(
    type: AssetType | None = None,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 25,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    items, total = await asset_svc.list_assets(
        pool, asset_type=type, page=page, page_size=page_size,
        q=q, status=status, owner=owner,
        created_after=created_after, created_before=created_before,
        tag=tag, organization_id=user.organization_id,
    )
    for a in items:
        a.metadata_ = redaction.redact_metadata(a.metadata_, a.type, user)
    return AssetListResponse(
        items=[AssetResponse.model_validate(a, from_attributes=True) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", summary="Full-text search across asset names and descriptions")
async def search_assets(
    q: str = "",
    types: str = "",
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    type_list = None
    if types:
        type_list = [AssetType(t.strip()) for t in types.split(",") if t.strip()]
    results = await asset_svc.search_assets(
        pool, q, types=type_list, organization_id=user.organization_id
    )
    for a in results:
        a.metadata_ = redaction.redact_metadata(a.metadata_, a.type, user)
    return [AssetResponse.model_validate(a, from_attributes=True) for a in results]


@router.post("/", response_model=AssetResponse, status_code=201, summary="Create an asset")
async def create_asset(
    data: AssetCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    if await workflow_service.requires_approval(
        pool, data.type, "create", organization_id=user.organization_id
    ):
        change = await workflow_service.submit(
            pool,
            action="create",
            asset_type=data.type,
            target_asset_id=None,
            title=data.name,
            payload=workflow_service.asset_create_payload(
                name=data.name,
                description=data.description,
                status=data.status.value,
                owner_id=data.owner_id,
                metadata=data.metadata_,
                tags=data.tags,
                criticality=data.criticality,
            ),
            user=user,
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending_approval",
                "pending_change_id": str(change.id),
                "action": "create",
                "asset_type": data.type.value,
            },
        )
    try:
        asset = await asset_svc.create_asset(
            pool,
            organization_id=user.organization_id,
            type=data.type,
            name=data.name,
            description=data.description,
            status=data.status.value,
            owner_id=data.owner_id,
            metadata_=data.metadata_,
            updated_by=user.id,
            tags=data.tags,
            criticality=data.criticality,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


@router.get("/{asset_id}", response_model=AssetResponse, summary="Fetch one asset by id")
async def get_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    asset = await asset_svc.get_asset(
        pool, asset_id, organization_id=user.organization_id
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset.metadata_ = redaction.redact_metadata(asset.metadata_, asset.type, user)
    return AssetResponse.model_validate(asset, from_attributes=True)


@router.put("/{asset_id}", response_model=AssetResponse, summary="Update an asset")
async def update_asset(
    asset_id: UUID,
    data: AssetUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    old = await asset_svc.get_asset(
        pool, asset_id, organization_id=user.organization_id
    )
    if not old:
        raise HTTPException(status_code=404, detail="Asset not found")
    kwargs = data.model_dump(exclude_unset=True)
    if "status" in kwargs and kwargs["status"]:
        kwargs["status"] = kwargs["status"].value
    if await workflow_service.requires_approval(
        pool, old.type, "update", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="update",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=kwargs.get("name") or old.name,
                payload=workflow_service.asset_update_payload(kwargs),
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending_approval",
                "pending_change_id": str(change.id),
                "action": "update",
                "asset_id": str(asset_id),
            },
        )
    try:
        asset = await asset_svc.update_asset(
            pool, asset_id, organization_id=user.organization_id, **kwargs, updated_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


@router.delete("/{asset_id}", status_code=204, summary="Delete an asset")
async def delete_asset(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await asset_svc.get_asset(
        pool, asset_id, organization_id=user.organization_id
    )
    if not old:
        raise HTTPException(status_code=404, detail="Asset not found")
    if await workflow_service.requires_approval(
        pool, old.type, "delete", organization_id=user.organization_id
    ):
        try:
            change = await workflow_service.submit(
                pool,
                action="delete",
                asset_type=old.type,
                target_asset_id=asset_id,
                title=old.name,
                payload={},
                user=user,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending_approval",
                "pending_change_id": str(change.id),
                "action": "delete",
                "asset_id": str(asset_id),
            },
        )
    deleted = await asset_svc.delete_asset(
        pool, asset_id, organization_id=user.organization_id
    )
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
