"""REST API for the control test ledger."""
from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import control_test_service

router = APIRouter(prefix="/api/controls", tags=["controls"])


class TestRunCreate(BaseModel):
    result: str  # pass | partial | fail
    method: str = "manual"
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = None
    evidence_url: str | None = None


def _serialize(run: dict) -> dict:
    return {
        **run,
        "id": str(run["id"]),
        "control_id": str(run["control_id"]),
        "organization_id": str(run["organization_id"]),
        "tested_by": str(run["tested_by"]) if run.get("tested_by") else None,
    }


@router.get("/overdue", summary="Controls overdue for testing (or never tested)")
async def overdue(
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    rows = await control_test_service.overdue_for_test(
        pool, organization_id=user.organization_id
    )
    return [{**r, "id": str(r["id"])} for r in rows]


@router.get("/{control_id}/test-runs", summary="A control's test history")
async def list_runs(
    control_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    runs = await control_test_service.list_test_runs(
        pool, control_id, organization_id=user.organization_id
    )
    return [_serialize(r) for r in runs]


@router.post("/{control_id}/test-runs", status_code=201, summary="Record a control test result")
async def record_run(
    control_id: UUID,
    data: TestRunCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    try:
        run = await control_test_service.record_test_run(
            pool, control_id,
            result=data.result, method=data.method, tested_by=user.id,
            period_start=data.period_start, period_end=data.period_end,
            notes=data.notes, evidence_url=data.evidence_url,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(run)
