import os
import uuid as uuid_mod
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from grcen.config import settings
from grcen.models.attachment import AttachmentKind
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.attachment import AttachmentCreate, AttachmentResponse
from grcen.services import attachment as att_svc
from grcen.services import audit_service as audit_svc

router = APIRouter(prefix="/api/assets/{asset_id}/attachments", tags=["attachments"])

_ATT_FIELDS = ["name", "kind", "url_or_path"]


@router.get("/", response_model=list[AttachmentResponse])
async def list_attachments(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    _user: User = Depends(require_permission(Permission.VIEW)),
):
    atts = await att_svc.list_attachments(pool, asset_id)
    return [AttachmentResponse.model_validate(a, from_attributes=True) for a in atts]


@router.post("/", response_model=AttachmentResponse, status_code=201)
async def create_attachment(
    asset_id: UUID,
    data: AttachmentCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    att = await att_svc.create_attachment(
        pool, asset_id=asset_id, kind=data.kind, name=data.name, url_or_path=data.url_or_path
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, _ATT_FIELDS),
    )
    return AttachmentResponse.model_validate(att, from_attributes=True)


def _sanitize_filename(raw: str | None) -> str:
    """Strip directory components and dangerous characters from an upload filename."""
    name = os.path.basename(raw or "uploaded_file")
    # Keep only safe characters
    import re
    name = re.sub(r"[^a-zA-Z0-9._\-]", "_", name)
    # Truncate the original portion to 200 chars
    return name[:200]


@router.post("/upload", response_model=AttachmentResponse, status_code=201)
async def upload_file(
    asset_id: UUID,
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    # Content-type allowlist
    allowed_types = {t.strip() for t in settings.ALLOWED_UPLOAD_TYPES.split(",")}
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="File type not allowed")

    upload_dir = os.path.join(settings.UPLOAD_DIR, str(asset_id))
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = _sanitize_filename(file.filename)
    filename = f"{uuid_mod.uuid4()}_{safe_name}"
    filepath = os.path.join(upload_dir, filename)

    # Path traversal check
    real_upload_dir = os.path.realpath(upload_dir)
    real_filepath = os.path.realpath(filepath)
    if not real_filepath.startswith(real_upload_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Chunked read with size limit
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)  # 64 KB chunks
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    with open(filepath, "wb") as f:
        f.write(content)

    att = await att_svc.create_attachment(
        pool,
        asset_id=asset_id,
        kind=AttachmentKind.FILE,
        name=file.filename or "uploaded_file",
        url_or_path=filepath,
    )
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="create",
        entity_type="attachment",
        entity_id=att.id,
        entity_name=att.name,
        changes=audit_svc.create_snapshot(att.__dict__, _ATT_FIELDS),
    )
    return AttachmentResponse.model_validate(att, from_attributes=True)


@router.delete("/{att_id}", status_code=204)
async def delete_attachment(
    asset_id: UUID,
    att_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await att_svc.get_attachment(pool, att_id)
    if not old or old.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    deleted = await att_svc.delete_attachment(pool, att_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await audit_svc.log_audit_event(
        pool,
        user_id=user.id,
        username=user.username,
        action="delete",
        entity_type="attachment",
        entity_id=old.id,
        entity_name=old.name,
        changes=audit_svc.delete_snapshot(old.__dict__, _ATT_FIELDS),
    )
