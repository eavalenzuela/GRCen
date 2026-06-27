"""Compliance framework dashboards, control library, and PDF/CSV reports."""
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers._pages_shared import (
    _csrf_check,
    templates,
)
from grcen.routers.deps import (
    get_db,
    require_permission,
)
from grcen.services import (
    access_log_service,
    alert_service as alert_svc,
    control_test_service,
    framework_service,
    pdf_service,
)


def _flash(flash: str | None) -> dict | None:
    if not flash:
        return None
    ok, _, message = flash.partition(":")
    return {"ok": ok == "ok", "message": message or flash}

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/frameworks", response_class=HTMLResponse)
async def frameworks_index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    frameworks = await framework_service.list_frameworks(pool, organization_id=user.organization_id)
    matrix = await framework_service.crosswalk_matrix(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "frameworks/index.html",
        context={
            "user": user, "frameworks": frameworks,
            "matrix": matrix, "notif_count": notif_count,
        },
    )


@router.get("/frameworks/{framework_id}", response_class=HTMLResponse)
async def framework_detail(
    request: Request,
    framework_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    detail = await framework_service.get_framework_detail(pool, framework_id, organization_id=user.organization_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Framework not found")
    last_audited = await framework_service._last_audited_for_requirements(
        pool, [r.id for r in detail.requirements]
    )
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "frameworks/detail.html",
        context={
            "user": user, "detail": detail,
            "last_audited": last_audited, "notif_count": notif_count,
        },
    )


@router.get("/frameworks/{framework_id}/gap-report.csv")
async def framework_gap_report_csv(
    framework_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    rows = await framework_service.gap_report_rows(
        pool, framework_id, organization_id=user.organization_id
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Framework not found")
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "requirement_id", "requirement_name", "coverage", "satisfied",
        "satisfier_count", "satisfiers", "borrowed_from", "last_audited",
    ])
    for r in rows:
        writer.writerow([
            r["requirement_id"], r["requirement_name"], r["coverage"], r["satisfied"],
            r["satisfier_count"], r["satisfiers"], r["borrowed_from"], r["last_audited"],
        ])
    await access_log_service.record(
        pool, user=user, action="export",
        entity_type="framework", entity_id=framework_id,
        entity_name=f"framework-{framework_id}-gap-report.csv",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="framework-{framework_id}-gap-report.csv"'
            ),
        },
    )


@router.get("/controls", response_class=HTMLResponse)
async def controls_library(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    """Inverted view: every Control with the requirements it covers + test status."""
    controls = await framework_service.list_controls_with_coverage(
        pool, organization_id=user.organization_id
    )
    control_ids = [c["id"] for c in controls]
    sparklines = await control_test_service.recent_results(
        pool, control_ids, organization_id=user.organization_id
    )
    overdue = await control_test_service.overdue_for_test(
        pool, organization_id=user.organization_id
    )
    overdue_ids = {str(o["id"]) for o in overdue}
    notif_count = await alert_svc.count_unread_notifications(
        pool, organization_id=user.organization_id, user_id=user.id
    )
    return templates.TemplateResponse(
        request, "frameworks/controls.html",
        context={
            "user": user, "controls": controls,
            "sparklines": {str(k): v for k, v in sparklines.items()},
            "overdue_count": len(overdue), "overdue_ids": overdue_ids,
            "flash": _flash(request.query_params.get("flash")),
            "notif_count": notif_count,
        },
    )


@router.post("/controls/{control_id}/test")
async def record_control_test(
    control_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    """Record a control test result from the control library form."""
    form = await request.form()
    result = str(form.get("result", "")).strip()
    notes = str(form.get("notes", "")).strip() or None
    try:
        await control_test_service.record_test_run(
            pool, control_id, result=result, method="manual",
            tested_by=user.id, notes=notes, organization_id=user.organization_id,
        )
        msg = f"ok:Recorded {result} test result."
    except ValueError as exc:
        msg = f"fail:{exc}"
    return RedirectResponse(f"/controls?flash={quote(msg)}", status_code=302)


@router.get("/frameworks/{framework_id}/gap-report.pdf")
async def framework_gap_report_pdf(
    framework_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    pdf = await pdf_service.render_framework_gap_report(
        pool, framework_id, organization_id=user.organization_id
    )
    if pdf is None:
        raise HTTPException(status_code=404, detail="Framework not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="framework", entity_id=framework_id,
        entity_name=f"framework-{framework_id}-gap-report.pdf",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="framework-{framework_id}-gap-report.pdf"'},
    )


@router.get("/assets/{asset_id}/audit-report.pdf")
async def audit_report_pdf(
    asset_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    """Per-audit dossier PDF — only valid when the asset's type is 'audit'."""
    pdf = await pdf_service.render_audit_report(
        pool, asset_id, organization_id=user.organization_id
    )
    if pdf is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="asset", entity_id=asset_id,
        entity_name=f"audit-{asset_id}.pdf",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit-{asset_id}.pdf"'},
    )


@router.get("/exports/assets.pdf")
async def assets_list_pdf(
    request: Request,
    type: str | None = None,
    q: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EXPORT)),
):
    """Filtered asset list as a single PDF dossier."""
    asset_type = AssetType(type) if type else None
    pdf = await pdf_service.render_asset_list_report(
        pool,
        organization_id=user.organization_id, user=user,
        asset_type=asset_type, q=q, status=status, tag=tag,
    )
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="asset", entity_id=None,
        entity_name="assets.pdf",
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="assets.pdf"'},
    )


@router.get("/frameworks/{framework_id}/report.pdf")
async def framework_report_pdf(
    framework_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    pdf = await pdf_service.render_framework_report(
        pool, framework_id, organization_id=user.organization_id,
    )
    if pdf is None:
        raise HTTPException(status_code=404, detail="Framework not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="framework", entity_id=framework_id,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="framework-{framework_id}.pdf"',
        },
    )


@router.get("/assets/{asset_id}/report.pdf")
async def asset_report_pdf(
    asset_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    pdf = await pdf_service.render_asset_report(
        pool, asset_id, user=user, organization_id=user.organization_id,
    )
    if pdf is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    await access_log_service.record(
        pool, user=user, action="pdf_export",
        entity_type="asset", entity_id=asset_id,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="asset-{asset_id}.pdf"',
        },
    )


# --- User self-service settings ---


