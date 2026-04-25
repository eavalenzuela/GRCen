"""REST API for compliance framework dashboards."""

from dataclasses import asdict
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import framework_service

router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


@router.get("/", summary="List frameworks with requirement counts and coverage")
async def list_frameworks(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    summaries = await framework_service.list_frameworks(pool, organization_id=user.organization_id)
    return [
        {
            **asdict(s),
            "id": str(s.id),
            "coverage_percent": s.coverage_percent,
        }
        for s in summaries
    ]


@router.get(
    "/{framework_id}",
    summary="Fetch a framework with requirements, audits, vendors, and in-scope assets",
)
async def get_framework(
    framework_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    detail = await framework_service.get_framework_detail(pool, framework_id, organization_id=user.organization_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Framework not found")
    return {
        "framework": {**detail.framework, "id": str(detail.framework["id"])},
        "coverage_percent": detail.coverage_percent,
        "satisfied_count": detail.satisfied_count,
        "gap_count": detail.gap_count,
        "requirements": [
            {
                "id": str(r.id),
                "name": r.name,
                "satisfied": r.satisfied,
                "satisfiers": [
                    {**s, "id": str(s["id"])} for s in r.satisfiers
                ],
            }
            for r in detail.requirements
        ],
        "audits": [{**a, "id": str(a["id"])} for a in detail.audits],
        "vendors": [{**v, "id": str(v["id"])} for v in detail.vendors],
        "in_scope_assets": [
            {**a, "id": str(a["id"])} for a in detail.in_scope_assets
        ],
    }
