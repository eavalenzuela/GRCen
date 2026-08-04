"""HTTP ingress for external-source syncs (roadmap I0/I2).

Lets an external ETL push a roster/inventory over HTTP instead of the CLI. Every
endpoint is authenticated with a GRCen API token scoped to the ``sync``
permission (org-bound, so a sync stays within that token's organization) and
returns the sync result as JSON. Business logic lives in the per-source sync
services (``hris_sync`` today; CMDB/scanner connectors slot in the same way).
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
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


async def _decode(upload: UploadFile | None) -> str | None:
    """Read an uploaded CSV as text. ``utf-8-sig`` strips Excel's BOM, which
    would otherwise turn the first header into ``﻿ref`` and break the file.
    """
    if upload is None:
        return None
    return (await upload.read()).decode("utf-8-sig")


@router.post("/upsert/csv", summary="Idempotent upsert from CSV files (multipart)")
async def sync_upsert_csv(
    request: Request,
    source: str = Form(..., description="Your source tag; must not collide with a built-in connector"),
    on_missing: str = Form("ignore", description="ignore | archive | delete"),
    default_type: str | None = Form(None, description="Asset type for rows with no 'type' column"),
    assets: UploadFile | None = File(None),
    relationships: UploadFile | None = File(None),
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """The CSV envelope over ``/api/sync/upsert`` — same contract, same lifecycle.

    Send an ``assets`` CSV, a ``relationships`` CSV, or both; sending both in one
    request keeps the sync in a single transaction, so relationships resolve
    against assets created in the same call. Query: ``dry_run``. See
    ``services.generic_sync`` for columns and semantics.
    """
    if assets is None and relationships is None:
        return JSONResponse(
            {"errors": ["attach an 'assets' and/or 'relationships' CSV file"]},
            status_code=400,
        )
    try:
        assets_csv = await _decode(assets)
        relationships_csv = await _decode(relationships)
    except UnicodeDecodeError:
        return JSONResponse({"errors": ["files must be UTF-8 encoded"]}, status_code=400)

    payload, parse_errors, parse_warnings = generic_sync.payload_from_csv(
        source=source,
        assets_csv=assets_csv,
        relationships_csv=relationships_csv,
        default_type=default_type,
        on_missing=on_missing,
    )
    if parse_errors:
        return JSONResponse({"errors": parse_errors}, status_code=400)

    result: GenericSyncResult = await generic_sync.sync(
        pool, payload,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
    )
    # Unrecognized-column notices belong with whatever the sync itself reported.
    result.warnings = [*parse_warnings, *result.warnings]
    status = 200 if not result.errors else 400
    return JSONResponse(dataclasses.asdict(result), status_code=status)


@router.post("/hris", summary="Sync an HRIS roster into the People graph")
async def sync_hris(
    request: Request,
    pool=Depends(get_db),
    user: User = Depends(_sync_required),
) -> JSONResponse:
    """Body = the roster (see ``services.hris_sync`` for the contract). Query:
    ``dry_run`` (preview, writes nothing), ``deactivate_leavers`` (default true),
    ``allow_empty`` (default false — an empty roster is refused rather than
    treated as "everyone left").
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
        allow_empty=_flag(request, "allow_empty", False),
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
    that dropped out are archived as remediated), ``allow_empty`` (default
    false — an empty export is refused rather than resolving every finding)."""
    try:
        export = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: VulnSyncResult = await vuln_sync.sync_findings(
        pool, export,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        resolve_missing=_flag(request, "resolve_missing", True),
        allow_empty=_flag(request, "allow_empty", False),
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
    contract). Query: ``dry_run``, ``resolve_missing`` (default true),
    ``allow_empty`` (default false)."""
    try:
        export = await request.json()
    except Exception:
        return JSONResponse({"errors": ["invalid JSON body"]}, status_code=400)

    result: CspmSyncResult = await cspm_sync.sync_findings(
        pool, export,
        organization_id=user.organization_id,
        dry_run=_flag(request, "dry_run", False),
        resolve_missing=_flag(request, "resolve_missing", True),
        allow_empty=_flag(request, "allow_empty", False),
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
