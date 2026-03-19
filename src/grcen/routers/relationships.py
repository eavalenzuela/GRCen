from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_db
from grcen.schemas.relationship import (
    RelationshipCreate,
    RelationshipResponse,
    RelationshipUpdate,
)
from grcen.services import relationship as rel_svc

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


@router.get("/", response_model=list[RelationshipResponse])
async def list_relationships(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rels = await rel_svc.list_relationships_for_asset(pool, asset_id)
    return [RelationshipResponse.model_validate(r, from_attributes=True) for r in rels]


@router.post("/", response_model=RelationshipResponse, status_code=201)
async def create_relationship(
    data: RelationshipCreate,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rel = await rel_svc.create_relationship(
        pool,
        source_asset_id=data.source_asset_id,
        target_asset_id=data.target_asset_id,
        relationship_type=data.relationship_type,
        description=data.description,
    )
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.get("/{rel_id}", response_model=RelationshipResponse)
async def get_relationship(
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rel = await rel_svc.get_relationship(pool, rel_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.put("/{rel_id}", response_model=RelationshipResponse)
async def update_relationship(
    rel_id: UUID,
    data: RelationshipUpdate,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    kwargs = data.model_dump(exclude_unset=True)
    rel = await rel_svc.update_relationship(pool, rel_id, **kwargs)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return RelationshipResponse.model_validate(rel, from_attributes=True)


@router.delete("/{rel_id}", status_code=204)
async def delete_relationship(
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    deleted = await rel_svc.delete_relationship(pool, rel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Relationship not found")
