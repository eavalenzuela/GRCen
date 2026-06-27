"""REST API for findings: overdue list + gated closure."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import findings_service

router = APIRouter(prefix="/api/findings", tags=["findings"])


class CloseFinding(BaseModel):
    verified_by: str | None = None


@router.get("/overdue", summary="Findings past their remediation due date")
async def overdue(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    rows = await findings_service.overdue_findings(pool, organization_id=user.organization_id)
    return [{**r, "id": str(r["id"])} for r in rows]


@router.post("/{finding_id}/close", summary="Close a finding (CAPA + verification gated)")
async def close(
    finding_id: UUID,
    data: CloseFinding,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    try:
        meta = await findings_service.close_finding(
            pool, finding_id,
            verified_by=data.verified_by or user.username,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"finding_status": meta["finding_status"], "verified_by": meta["verified_by"]}
