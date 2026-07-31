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
from grcen.services import (
    cmdb_sync,
    cspm_sync,
    generic_sync,
    hris_sync,
    ticketing_service,
    vuln_sync,
)
from grcen.services.cmdb_sync import CmdbSyncResult
from grcen.services.cspm_sync import CspmSyncResult
from grcen.services.generic_sync import GenericSyncResult
from grcen.services.hris_sync import HrisSyncResult
from grcen.services.vuln_sync import VulnSyncResult

router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_required = require_permission(Permission.SYNC)


def _flag(request: Request, name: str, default: bool) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@router.post("/upsert", summary="Idempotent upsert of arbitrary assets/relationships by external ref")
async def sync_upsert(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Generic, source-agnostic ingress for callers with no matching connector.

    Body carries its own ``source`` tag plus ``assets`` / ``relationships``
    keyed by caller-defined ``ref``s; re-posting upserts in place on
    ``(organization_id, source, source_ref)``. See ``services.generic_sync`` for
    the contract. Query: ``dry_run`` (preview, writes nothing). Lifecycle for
    records the payload drops is the body's ``on_missing`` field, not a query
    flag — it defaults to ``ignore`` so partial uploads are safe.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: GenericSyncResult = await generic_sync.sync(
        pool, payload,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)


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


@router.post("/cspm", summary="Sync a CSPM misconfiguration export into the graph")
async def sync_cspm(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = the CSPM findings export (see ``services.cspm_sync`` for the
    contract). Query: ``dry_run``, ``resolve_missing`` (default true)."""
    try:
        export = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: CspmSyncResult = await cspm_sync.sync_findings(
        pool, export,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        resolve_missing=_flag(request, "resolve_missing", True),
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)


@router.post("/cmdb", summary="Sync a CMDB / cloud inventory into the graph")
async def sync_cmdb(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = the inventory (see ``services.cmdb_sync`` for the contract). Query:
    ``dry_run`` (preview), ``archive_missing`` (default true — CIs that dropped
    out are archived as decommissioned)."""
    try:
        inventory = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: CmdbSyncResult = await cmdb_sync.sync_inventory(
        pool, inventory,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        archive_missing=_flag(request, "archive_missing", True),
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)


@router.post("/tickets", summary="Sync external ticket statuses onto findings")
async def sync_tickets(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = ``{"tickets": [{"ticket_key": "...", "status": "..."}]}``. Maps
    each ticket's status onto the finding it's linked to (E3 inbound)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    tickets = body.get("tickets") if isinstance(body, dict) else None
    result = await ticketing_service.sync_ticket_statuses(
        pool, tickets, organization_id=user.organization_id
    )
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)
