from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.graph import GraphResponse
from grcen.services import org_views as org_views_svc

router = APIRouter(prefix="/api/org-views", tags=["org-views"])


@router.get("/org-chart", response_model=GraphResponse)
async def org_chart(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    return await org_views_svc.get_org_chart(pool)


@router.get("/business-structure", response_model=GraphResponse)
async def business_structure(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    return await org_views_svc.get_business_structure(pool)


@router.get("/products")
async def list_products(
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    return await org_views_svc.list_products(pool)


@router.get("/product/{product_id}", response_model=GraphResponse)
async def product_view(
    product_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    return await org_views_svc.get_product_view(pool, product_id)
