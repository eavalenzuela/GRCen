"""REST API for saved searches."""

from dataclasses import asdict
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException

from grcen.models.user import User
from grcen.routers.deps import get_current_user, get_db
from grcen.services import saved_search_service as ss_svc

router = APIRouter(prefix="/api/saved-searches", tags=["saved-searches"])


@router.get("/", summary="List the current user's saved searches plus shared ones")
async def list_saved_searches(
    path: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await ss_svc.list_visible(
        pool, user.id, organization_id=user.organization_id, path=path
    )
    return [
        {**asdict(s), "id": str(s.id), "user_id": str(s.user_id), "href": s.href}
        for s in items
    ]


@router.post("/", status_code=201, summary="Create a saved search")
async def create_saved_search(
    payload: dict = Body(...),
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = str(payload.get("name", "")).strip()
    path = str(payload.get("path", "")).strip()
    if not name or not path:
        raise HTTPException(status_code=400, detail="name and path are required")
    created = await ss_svc.create_saved_search(
        pool,
        user_id=user.id,
        organization_id=user.organization_id,
        name=name,
        path=path,
        query_string=str(payload.get("query_string", "")),
        shared=bool(payload.get("shared", False)),
    )
    return {
        **asdict(created),
        "id": str(created.id),
        "user_id": str(created.user_id),
        "href": created.href,
    }


@router.delete("/{search_id}", status_code=204, summary="Delete a saved search")
async def delete_saved_search(
    search_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ok = await ss_svc.delete_saved_search(
        pool, search_id, user.id, is_admin=user.is_admin,
        organization_id=user.organization_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Saved search not found")
