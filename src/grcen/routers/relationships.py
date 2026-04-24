from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.relationship import (
    RelationshipCreate,
    RelationshipResponse,
    RelationshipUpdate,
)
from grcen.models.asset import AssetType
from grcen.services import asset as asset_svc
from grcen.services import relationship as rel_svc
from grcen.services import audit_service as audit_svc

router = APIRouter(prefix="/api/relationships", tags=["relationships"])

_REL_FIELDS = ["relationship_type", "description"]


@router.get(
    "/",
    response_model=list[RelationshipResponse],
    summary="List relationships touching a given asset",
)
async def list_relationships(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id)
    return [RelationshipResponse.model_validate(r, from_attributes=True) for r in rels]


@router.post(
    "/",
    response_model=RelationshipResponse,
    status_code=201,
    summary="Create a relationship between two assets",
)
async def create_relationship(
    data: RelationshipCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    # Auto-convert owns→manages when target is a Person
    rel_type = data.relationship_type
    if rel_type == "owns":
        target = await asset_svc.get_asset(pool, data.target_asset_id)
        if target and target.type == AssetType.PERSON:
            rel_type = "manages"

    rel = await rel_svc.create_relationship(
        pool,
        source_asset_id=data.source_asset_id,
        target_asset_id=data.target_asset_id,
        relationship_type=rel_type,
        description=data.description,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="relationship",
        entity_id=rel.id,
        entity_name=rel.relationship_type,
        changes=audit_svc.create_snapshot(rel.__dict__, _REL_FIELDS),
    )
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.get(
    "/{rel_id}",
    response_model=RelationshipResponse,
    summary="Fetch one relationship by id",
)
async def get_relationship(
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    rel = await rel_svc.get_relationship(pool, rel_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.put(
    "/{rel_id}",
    response_model=RelationshipResponse,
    summary="Update a relationship",
)
async def update_relationship(
    rel_id: UUID,
    data: RelationshipUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    old = await rel_svc.get_relationship(pool, rel_id)
    if not old:
        raise HTTPException(status_code=404, detail="Relationship not found")
    kwargs = data.model_dump(exclude_unset=True)
    rel = await rel_svc.update_relationship(pool, rel_id, **kwargs)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    diff = audit_svc.compute_diff(old.__dict__, rel.__dict__, _REL_FIELDS)
    if diff:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="update",
            entity_type="relationship",
            entity_id=rel.id,
            entity_name=rel.relationship_type,
            changes=diff,
        )
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.delete("/{rel_id}", status_code=204, summary="Delete a relationship")
async def delete_relationship(
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await rel_svc.get_relationship(pool, rel_id)
    if not old:
        raise HTTPException(status_code=404, detail="Relationship not found")
    deleted = await rel_svc.delete_relationship(pool, rel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Relationship not found")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="relationship",
        entity_id=old.id,
        entity_name=old.relationship_type,
        changes=audit_svc.delete_snapshot(old.__dict__, _REL_FIELDS),
    )
