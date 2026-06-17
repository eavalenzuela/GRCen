"""Relationship evidence/attachment pages."""
import os
import uuid
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from grcen.config import settings
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
    alert_service as alert_svc,
    asset as asset_svc,
    attachment as att_svc,
    audit_service as audit_svc,
    relationship as rel_svc,
)

router = APIRouter(tags=["pages"], dependencies=[Depends(_csrf_check)])

@router.get("/relationships/{rel_id}/edit", response_class=HTMLResponse)
async def relationship_edit_page(
    request: Request,
    rel_id: UUID,
    from_: str | None = Query(None, alias="from"),
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    rel = await rel_svc.get_relationship(pool, rel_id, organization_id=user.organization_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    rel_types = await rel_svc.list_relationship_types(pool, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "relationships/edit.html",
        context={
            "user": user,
            "rel": rel,
            "rel_types": rel_types,
            "return_to": from_ or str(rel.source_asset_id),
            "notif_count": notif_count,
        },
    )


@router.post("/relationships/{rel_id}/edit")
async def relationship_edit_submit(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.EDIT)),
):
    form = await request.form()
    rel_type = str(form.get("relationship_type", "")).strip()
    description = str(form.get("description", "")).strip()
    return_to = str(form.get("return_to", "")).strip()
    if not rel_type:
        raise HTTPException(status_code=400, detail="Relationship type is required")
    old = await rel_svc.get_relationship(pool, rel_id, organization_id=user.organization_id)
    if not old:
        raise HTTPException(status_code=404, detail="Relationship not found")
    rel = await rel_svc.update_relationship(
        pool, rel_id,
        relationship_type=rel_type,
        description=description,
        organization_id=user.organization_id,
    )
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    diff = audit_svc.compute_diff(
        old.__dict__, rel.__dict__, ["relationship_type", "description"]
    )
    if diff:
        await audit_svc.log_audit_event(
            pool,
            user_id=user.id,
            username=user.username,
            action="update",
            entity_type="relationship",
            entity_id=rel.id,
            entity_name=rel.relationship_type,
            changes=diff,
        )
    dest = return_to or str(rel.source_asset_id)
    return RedirectResponse(f"/assets/{dest}", status_code=302)


@router.get("/relationships/{rel_id}/evidence", response_class=HTMLResponse)
async def relationship_evidence_page(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    rel = await rel_svc.get_relationship(pool, rel_id, organization_id=user.organization_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    source = await asset_svc.get_asset(pool, rel.source_asset_id, organization_id=user.organization_id)
    target = await asset_svc.get_asset(pool, rel.target_asset_id, organization_id=user.organization_id)
    attachments = await att_svc.list_attachments_for_relationship(pool, rel_id, organization_id=user.organization_id)
    notif_count = await alert_svc.count_unread_notifications(pool, organization_id=user.organization_id, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "relationships/evidence.html",
        context={
            "user": user,
            "rel": rel,
            "source": source,
            "target": target,
            "attachments": attachments,
            "notif_count": notif_count,
        },
    )


@router.post("/relationships/{rel_id}/evidence")
async def relationship_evidence_create(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    from grcen.models.attachment import AttachmentKind

    form = await request.form()
    raw_kind = str(form.get("kind", "url"))
    try:
        kind = AttachmentKind(raw_kind)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid attachment kind: {raw_kind!r}")
    name = str(form.get("name", "")).strip()
    url_or_path = str(form.get("url_or_path", "")).strip()
    if not name or not url_or_path:
        raise HTTPException(status_code=400, detail="Name and URL/path are required")
    att = await att_svc.create_attachment(
        pool,
        organization_id=user.organization_id,
        relationship_id=rel_id,
        kind=kind,
        name=name,
        url_or_path=url_or_path,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


@router.post("/relationships/{rel_id}/evidence/upload")
async def relationship_evidence_upload(
    request: Request,
    rel_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    from grcen.models.attachment import AttachmentKind
    from grcen.routers.attachments import (
        _read_upload,
        _sanitize_filename,
        _write_upload,
    )

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="No file uploaded")
    content, encrypted = await _read_upload(pool, upload)
    filename = f"{uuid.uuid4()}_{_sanitize_filename(upload.filename)}"
    owner_dir = os.path.join(settings.UPLOAD_DIR, "relationships", str(rel_id))
    filepath = _write_upload(content, owner_dir, filename)
    att = await att_svc.create_attachment(
        pool,
        organization_id=user.organization_id,
        relationship_id=rel_id,
        kind=AttachmentKind.FILE,
        name=upload.filename or "uploaded_file",
        url_or_path=filepath,
        encrypted=encrypted,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


@router.post("/relationships/{rel_id}/evidence/{att_id}/delete")
async def relationship_evidence_delete(
    rel_id: UUID,
    att_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    att = await att_svc.get_attachment(pool, att_id, organization_id=user.organization_id)
    if not att or att.relationship_id != rel_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await att_svc.delete_attachment(pool, att_id, organization_id=user.organization_id)
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.delete_snapshot(att.__dict__, ["name", "kind", "url_or_path"]),
    )
    return RedirectResponse(f"/relationships/{rel_id}/evidence", status_code=302)


# --- Data access log ---


