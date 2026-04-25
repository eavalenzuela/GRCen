import uuid
from uuid import UUID

import asyncpg

from grcen.models.attachment import Attachment, AttachmentKind


async def create_attachment(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    asset_id: UUID | None = None,
    relationship_id: UUID | None = None,
    kind: AttachmentKind,
    name: str,
    url_or_path: str | None = None,
    encrypted: bool = False,
) -> Attachment:
    if (asset_id is None) == (relationship_id is None):
        raise ValueError(
            "Exactly one of asset_id or relationship_id must be provided"
        )
    if asset_id is not None:
        owner = await pool.fetchrow(
            "SELECT organization_id FROM assets WHERE id = $1", asset_id
        )
    else:
        owner = await pool.fetchrow(
            "SELECT organization_id FROM relationships WHERE id = $1", relationship_id
        )
    if owner is None:
        raise ValueError("Attachment owner not found")
    if organization_id is None:
        organization_id = owner["organization_id"]
    elif owner["organization_id"] != organization_id:
        raise ValueError("Attachment owner is in a different organization")
    row = await pool.fetchrow(
        """
        INSERT INTO attachments
            (id, asset_id, relationship_id, kind, name, url_or_path, encrypted, organization_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        uuid.uuid4(),
        asset_id,
        relationship_id,
        kind.value,
        name,
        url_or_path,
        encrypted,
        organization_id,
    )
    return Attachment.from_row(row)


async def list_attachments(
    pool: asyncpg.Pool, asset_id: UUID, *, organization_id: UUID | None = None
) -> list[Attachment]:
    rows = await pool.fetch(
        """SELECT * FROM attachments
           WHERE asset_id = $1 AND ($2::uuid IS NULL OR organization_id = $2)
           ORDER BY created_at""",
        asset_id,
        organization_id,
    )
    return [Attachment.from_row(r) for r in rows]


async def list_attachments_for_relationship(
    pool: asyncpg.Pool, relationship_id: UUID, *, organization_id: UUID | None = None
) -> list[Attachment]:
    rows = await pool.fetch(
        """SELECT * FROM attachments
           WHERE relationship_id = $1 AND ($2::uuid IS NULL OR organization_id = $2)
           ORDER BY created_at""",
        relationship_id,
        organization_id,
    )
    return [Attachment.from_row(r) for r in rows]


async def get_attachment(
    pool: asyncpg.Pool, att_id: UUID, *, organization_id: UUID | None = None
) -> Attachment | None:
    row = await pool.fetchrow(
        """SELECT * FROM attachments
           WHERE id = $1 AND ($2::uuid IS NULL OR organization_id = $2)""",
        att_id, organization_id,
    )
    return Attachment.from_row(row) if row else None


async def delete_attachment(
    pool: asyncpg.Pool, att_id: UUID, *, organization_id: UUID | None = None
) -> bool:
    if organization_id is not None:
        result = await pool.execute(
            "DELETE FROM attachments WHERE id = $1 AND organization_id = $2",
            att_id, organization_id,
        )
    else:
        result = await pool.execute("DELETE FROM attachments WHERE id = $1", att_id)
    return result == "DELETE 1"
