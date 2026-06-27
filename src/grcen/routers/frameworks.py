"""REST API for compliance framework dashboards."""

from dataclasses import asdict
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import compliance_snapshot_service, framework_service

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
            "effective_satisfied_count": s.effective_satisfied_count,
            "effective_coverage_percent": s.effective_coverage_percent,
        }
        for s in summaries
    ]


@router.get("/crosswalk-matrix", summary="Framework×framework cross_maps edge counts")
async def crosswalk_matrix(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    return await framework_service.crosswalk_matrix(pool, organization_id=user.organization_id)


@router.get("/{framework_id}/coverage-timeline", summary="Daily coverage snapshots for a framework")
async def coverage_timeline(
    framework_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    return await compliance_snapshot_service.get_coverage_timeline(
        pool, framework_id, organization_id=user.organization_id
    )


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
        "effective_coverage_percent": detail.effective_coverage_percent,
        "satisfied_count": detail.satisfied_count,
        "borrowed_count": detail.borrowed_count,
        "gap_count": detail.gap_count,
        "crosswalk_count": detail.crosswalk_count,
        "requirements": [
            {
                "id": str(r.id),
                "name": r.name,
                "satisfied": r.satisfied,
                "coverage": r.coverage,
                "satisfiers": [
                    {**s, "id": str(s["id"])} for s in r.satisfiers
                ],
                "crosswalks": [
                    {**cw, "id": str(cw["id"])} for cw in r.crosswalks
                ],
                "borrowed_from": [
                    {**b, "id": str(b["id"])} for b in r.borrowed_from
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
