import os
import re
import uuid as uuid_mod
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response

from grcen.config import settings
from grcen.models.attachment import AttachmentKind
from grcen.models.user import User
from grcen.permissions import Permission
from grcen.routers.deps import get_db, require_permission
from grcen.schemas.attachment import AttachmentCreate, AttachmentResponse
from grcen.services import (
    access_log_service,
    attachment as att_svc,
    audit_service as audit_svc,
    encryption_config,
)
from grcen.services.encryption import decrypt_bytes, encrypt_bytes

router = APIRouter(prefix="/api/assets/{asset_id}/attachments", tags=["attachments"])
rel_router = APIRouter(
    prefix="/api/relationships/{relationship_id}/attachments", tags=["attachments"]
)

_ATT_FIELDS = ["name", "kind", "url_or_path"]


def _sanitize_filename(raw: str | None) -> str:
    """Strip directory components and dangerous characters from an upload filename."""
    name = os.path.basename(raw or "uploaded_file")
    name = re.sub(r"[^a-zA-Z0-9._\-]", "_", name)
    return name[:200]


def _display_name(raw: str | None) -> str:
    """The attachment's human name; the column is VARCHAR(255)."""
    return (raw or "uploaded_file")[:255]


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _reject_file_kind(data: AttachmentCreate) -> None:
    """``kind=file`` rows are only ever created by the upload routes.

    Accepting them from the JSON API let a caller point ``url_or_path`` at any
    file on the server and then fetch it through the download route.
    """
    if data.kind == AttachmentKind.FILE:
        raise HTTPException(
            status_code=400,
            detail="File attachments must be uploaded via the /upload endpoint",
        )


def _assert_in_upload_dir(path: str, *owner_parts: str) -> None:
    """Refuse to serve anything outside the owning object's own upload folder.

    Uploads are written to ``UPLOAD_DIR/<asset_id>/`` or
    ``UPLOAD_DIR/relationships/<relationship_id>/``; a row pointing anywhere
    else was not created by the upload routes.
    """
    root = os.path.realpath(os.path.join(settings.UPLOAD_DIR, *owner_parts))
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        raise HTTPException(status_code=404, detail="File not found on disk")


def _content_disposition(name: str) -> str:
    """RFC 6266 attachment header that survives quotes, backslashes and non-ASCII."""
    from urllib.parse import quote

    cleaned = "".join(ch for ch in name if ch >= " " and ch != "\x7f") or "download"
    ascii_fallback = cleaned.encode("ascii", "replace").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "\\\\").replace('"', '\\"')
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(cleaned)}"


# Leading bytes a file of the declared type must start with. Text-like types
# are checked for valid UTF-8 without NUL bytes instead.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
}
_TEXT_TYPES = {"text/plain", "text/csv", "application/json", "application/xml"}


def _check_content_matches_type(content: bytes, declared: str | None) -> None:
    """The browser-supplied Content-Type is a claim; make the bytes back it up."""
    if not declared:
        return
    if declared in _MAGIC:
        if not any(content.startswith(sig) for sig in _MAGIC[declared]):
            raise HTTPException(
                status_code=415, detail="File content does not match its declared type"
            )
    elif declared in _TEXT_TYPES:
        # Any text encoding is fine (exports from spreadsheets are often
        # cp1252 or UTF-16); what must not pass as text is a binary blob.
        has_wide_bom = content.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff"))
        if b"\x00" in content and not has_wide_bom:
            raise HTTPException(
                status_code=415, detail="File content does not match its declared type"
            )


async def _read_upload(pool: asyncpg.Pool, file: UploadFile) -> tuple[bytes, bool]:
    """Enforce the content-type allowlist + size limit and return (bytes, encrypted)."""
    allowed_types = {t.strip() for t in settings.ALLOWED_UPLOAD_TYPES.split(",")}
    if not file.content_type or file.content_type not in allowed_types:
        # Fail closed: a part with no declared type would otherwise skip both
        # the allowlist and the content check.
        raise HTTPException(status_code=415, detail="File type not allowed")

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
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
    _check_content_matches_type(content, file.content_type)

    file_encrypted = False
    if await encryption_config.is_scope_active(pool, "file_contents"):
        content = encrypt_bytes(content, "file_contents")
        file_encrypted = True
    return content, file_encrypted


def _write_upload(content: bytes, owner_dir: str, filename: str) -> str:
    os.makedirs(owner_dir, exist_ok=True)
    filepath = os.path.join(owner_dir, filename)
    real_dir = os.path.realpath(owner_dir)
    real_path = os.path.realpath(filepath)
    if not real_path.startswith(real_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename")
    with open(filepath, "wb") as f:
        f.write(content)
    return filepath


@router.get("/", response_model=list[AttachmentResponse])
async def list_attachments(
    asset_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    atts = await att_svc.list_attachments(pool, asset_id, organization_id=user.organization_id)
    return [AttachmentResponse.model_validate(a, from_attributes=True) for a in atts]


@router.post("/", response_model=AttachmentResponse, status_code=201)
async def create_attachment(
    asset_id: UUID,
    data: AttachmentCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    _reject_file_kind(data)
    att = await att_svc.create_attachment(
        pool, organization_id=user.organization_id, asset_id=asset_id,
        kind=data.kind, name=data.name, url_or_path=data.url_or_path,
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


@router.post("/upload", response_model=AttachmentResponse, status_code=201)
async def upload_file(
    asset_id: UUID,
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    content, encrypted = await _read_upload(pool, file)
    safe_name = _sanitize_filename(file.filename)
    filename = f"{uuid_mod.uuid4()}_{safe_name}"
    filepath = _write_upload(
        content, os.path.join(settings.UPLOAD_DIR, str(asset_id)), filename
    )
    try:
        att = await att_svc.create_attachment(
            pool,
            organization_id=user.organization_id,
            asset_id=asset_id,
            kind=AttachmentKind.FILE,
            name=_display_name(file.filename),
            url_or_path=filepath,
            encrypted=encrypted,
        )
    except Exception:
        _remove_quietly(filepath)
        raise
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


@router.get("/{att_id}/download")
async def download_file(
    asset_id: UUID,
    att_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    att = await att_svc.get_attachment(pool, att_id, organization_id=user.organization_id)
    if not att or att.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.kind != AttachmentKind.FILE or not att.url_or_path:
        raise HTTPException(status_code=400, detail="Not a downloadable file")
    _assert_in_upload_dir(att.url_or_path, str(asset_id))
    if not os.path.isfile(att.url_or_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    with open(att.url_or_path, "rb") as f:
        data = f.read()

    if att.encrypted:
        data = decrypt_bytes(data, "file_contents")

    await access_log_service.record(
        pool, user=user, action="download",
        entity_type="attachment", entity_id=att.id, entity_name=att.name,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(att.name)},
    )


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
    deleted = await att_svc.delete_attachment(pool, att_id, organization_id=user.organization_id)
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


# ── Relationship-owned attachments ────────────────────────────────────────


@rel_router.get(
    "/",
    response_model=list[AttachmentResponse],
    summary="List attachments on a relationship",
)
async def list_rel_attachments(
    relationship_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    atts = await att_svc.list_attachments_for_relationship(pool, relationship_id, organization_id=user.organization_id)
    return [AttachmentResponse.model_validate(a, from_attributes=True) for a in atts]


@rel_router.post(
    "/",
    response_model=AttachmentResponse,
    status_code=201,
    summary="Attach a URL or document reference to a relationship",
)
async def create_rel_attachment(
    relationship_id: UUID,
    data: AttachmentCreate,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    _reject_file_kind(data)
    att = await att_svc.create_attachment(
        pool,
        organization_id=user.organization_id,
        relationship_id=relationship_id,
        kind=data.kind,
        name=data.name,
        url_or_path=data.url_or_path,
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


@rel_router.post(
    "/upload",
    response_model=AttachmentResponse,
    status_code=201,
    summary="Upload a file as evidence on a relationship",
)
async def upload_rel_file(
    relationship_id: UUID,
    file: UploadFile,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.CREATE)),
):
    content, encrypted = await _read_upload(pool, file)
    safe_name = _sanitize_filename(file.filename)
    filename = f"{uuid_mod.uuid4()}_{safe_name}"
    owner_dir = os.path.join(settings.UPLOAD_DIR, "relationships", str(relationship_id))
    filepath = _write_upload(content, owner_dir, filename)
    try:
        att = await att_svc.create_attachment(
            pool,
            organization_id=user.organization_id,
            relationship_id=relationship_id,
            kind=AttachmentKind.FILE,
            name=_display_name(file.filename),
            url_or_path=filepath,
            encrypted=encrypted,
        )
    except Exception:
        _remove_quietly(filepath)
        raise
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


@rel_router.get("/{att_id}/download", summary="Download a relationship's file attachment")
async def download_rel_file(
    relationship_id: UUID,
    att_id: UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.VIEW)),
):
    att = await att_svc.get_attachment(pool, att_id, organization_id=user.organization_id)
    if not att or att.relationship_id != relationship_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if att.kind != AttachmentKind.FILE or not att.url_or_path:
        raise HTTPException(status_code=400, detail="Not a downloadable file")
    _assert_in_upload_dir(att.url_or_path, "relationships", str(relationship_id))
    if not os.path.isfile(att.url_or_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    with open(att.url_or_path, "rb") as f:
        data = f.read()
    if att.encrypted:
        data = decrypt_bytes(data, "file_contents")
    await access_log_service.record(
        pool, user=user, action="download",
        entity_type="attachment", entity_id=att.id, entity_name=att.name,
        path=str(request.url.path),
        ip_address=request.client.host if request.client else None,
    )
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(att.name)},
    )


@rel_router.delete("/{att_id}", status_code=204, summary="Delete a relationship attachment")
async def delete_rel_attachment(
    relationship_id: UUID,
    att_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
    user: User = Depends(require_permission(Permission.DELETE)),
):
    old = await att_svc.get_attachment(pool, att_id)
    if not old or old.relationship_id != relationship_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    deleted = await att_svc.delete_attachment(pool, att_id, organization_id=user.organization_id)
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
