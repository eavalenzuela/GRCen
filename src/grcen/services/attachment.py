import uuid
from uuid import UUID

import asyncpg

from grcen.models.attachment import Attachment, AttachmentKind


async def create_attachment(
    pool: asyncpg.Pool,
    *,
    asset_id: UUID,
    kind: AttachmentKind,
    name: str,
    url_or_path: str | None = None,
    encrypted: bool = False,
) -> Attachment:
    row = await pool.fetchrow(
        """
        INSERT INTO attachments (id, asset_id, kind, name, url_or_path, encrypted)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        uuid.uuid4(),
        asset_id,
        kind.value,
        name,
        url_or_path,
        encrypted,
    )
    return Attachment.from_row(row)


async def list_attachments(pool: asyncpg.Pool, asset_id: UUID) -> list[Attachment]:
    rows = await pool.fetch(
        "SELECT * FROM attachments WHERE asset_id = $1 ORDER BY created_at",
        asset_id,
    )
    return [Attachment.from_row(r) for r in rows]


async def get_attachment(pool: asyncpg.Pool, att_id: UUID) -> Attachment | None:
    row = await pool.fetchrow("SELECT * FROM attachments WHERE id = $1", att_id)
    return Attachment.from_row(row) if row else None


async def delete_attachment(pool: asyncpg.Pool, att_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM attachments WHERE id = $1", att_id)
    return result == "DELETE 1"
