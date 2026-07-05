"""HTTP ingress for external-source syncs (roadmap I0/I2).

Lets an external ETL push a roster/inventory over HTTP instead of the CLI. Every
endpoint is authenticated with a GRCen API token scoped to the ``sync``
permission (org-bound, so a sync stays within that token's organization) and
returns the sync result as JSON. Business logic lives in the per-source sync
services (``hris_sync`` today; CMDB/scanner connectors slot in the same way).
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.services import hris_sync, vuln_sync
from grcen.services.hris_sync import HrisSyncResult
from grcen.services.vuln_sync import VulnSyncResult

router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_required = require_permission(Permission.SYNC)


def _flag(request: Request, name: str, default: bool) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@router.post("/hris", summary="Sync an HRIS roster into the People graph")
async def sync_hris(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = the roster (see ``services.hris_sync`` for the contract). Query:
    ``dry_run`` (preview, writes nothing), ``deactivate_leavers`` (default true).
    """
    try:
        roster = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: HrisSyncResult = await hris_sync.sync_roster(
        pool, roster,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        deactivate_leavers=_flag(request, "deactivate_leavers", True),
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)


@router.post("/vulns", summary="Sync a vuln-findings export (uvt/scanner) into the graph")
async def sync_vulns(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = the findings export (see ``services.vuln_sync`` for the contract).
    Query: ``dry_run`` (preview), ``resolve_missing`` (default true — findings
    that dropped out are archived as remediated)."""
    try:
        export = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: VulnSyncResult = await vuln_sync.sync_findings(
        pool, export,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        resolve_missing=_flag(request, "resolve_missing", True),
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)
