from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.graph import GraphResponse
from grcen.services.graph import get_asset_graph, get_org_graph

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get(
    "",
    response_model=GraphResponse,
    summary="Fetch the whole-organization graph (capped at `limit` nodes)",
)
async def org_graph(
    limit: int = 500,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    limit = max(1, min(limit, 1000))
    return await get_org_graph(pool, user.organization_id, limit=limit)


@router.get(
    "/{asset_id}",
    response_model=GraphResponse,
    summary="Fetch the N-hop subgraph centered on an asset",
)
async def graph(
    asset_id: UUID,
    depth: int = 1,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW_GRAPH)),
):
    return await get_asset_graph(pool, asset_id, depth, organization_id=user.organization_id)
