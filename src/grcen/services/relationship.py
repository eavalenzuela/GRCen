import uuid
from uuid import UUID

import asyncpg

from grcen.models.asset import Asset
from grcen.models.relationship import Relationship


async def create_relationship(
    pool: asyncpg.Pool,
    *,
    source_asset_id: UUID,
    target_asset_id: UUID,
    relationship_type: str,
    description: str | None = None,
) -> Relationship:
    row = await pool.fetchrow(
        """
        INSERT INTO relationships
            (id, source_asset_id, target_asset_id, relationship_type, description)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        uuid.uuid4(),
        source_asset_id,
        target_asset_id,
        relationship_type,
        description,
    )
    return Relationship.from_row(row)


async def get_relationship(pool: asyncpg.Pool, rel_id: UUID) -> Relationship | None:
    row = await pool.fetchrow(
        """
        SELECT r.*,
               s.id as s_id, s.type as s_type, s.name as s_name, s.description as s_description,
               s.status as s_status, s.owner as s_owner, s.metadata as s_metadata,
               s.created_at as s_created_at, s.updated_at as s_updated_at,
               t.id as t_id, t.type as t_type, t.name as t_name, t.description as t_description,
               t.status as t_status, t.owner as t_owner, t.metadata as t_metadata,
               t.created_at as t_created_at, t.updated_at as t_updated_at
        FROM relationships r
        JOIN assets s ON s.id = r.source_asset_id
        JOIN assets t ON t.id = r.target_asset_id
        WHERE r.id = $1
        """,
        rel_id,
    )
    if not row:
        return None
    rel = Relationship.from_row(row)
    rel.source_asset = _asset_from_prefixed(row, "s_")
    rel.target_asset = _asset_from_prefixed(row, "t_")
    return rel


async def list_relationships_for_asset(
    pool: asyncpg.Pool, asset_id: UUID
) -> list[Relationship]:
    rows = await pool.fetch(
        """
        SELECT r.*,
               s.id as s_id, s.type as s_type, s.name as s_name, s.description as s_description,
               s.status as s_status, s.owner as s_owner, s.metadata as s_metadata,
               s.created_at as s_created_at, s.updated_at as s_updated_at,
               t.id as t_id, t.type as t_type, t.name as t_name, t.description as t_description,
               t.status as t_status, t.owner as t_owner, t.metadata as t_metadata,
               t.created_at as t_created_at, t.updated_at as t_updated_at
        FROM relationships r
        JOIN assets s ON s.id = r.source_asset_id
        JOIN assets t ON t.id = r.target_asset_id
        WHERE r.source_asset_id = $1 OR r.target_asset_id = $1
        ORDER BY r.created_at
        """,
        asset_id,
    )
    results = []
    for row in rows:
        rel = Relationship.from_row(row)
        rel.source_asset = _asset_from_prefixed(row, "s_")
        rel.target_asset = _asset_from_prefixed(row, "t_")
        results.append(rel)
    return results


async def update_relationship(
    pool: asyncpg.Pool,
    rel_id: UUID,
    *,
    relationship_type: str | None = None,
    description: str | None = None,
) -> Relationship | None:
    sets: list[str] = []
    vals: list = []
    idx = 1
    for col, val in [("relationship_type", relationship_type), ("description", description)]:
        if val is not None:
            sets.append(f"{col} = ${idx}")
            vals.append(val)
            idx += 1
    if not sets:
        return await get_relationship(pool, rel_id)
    sets.append("updated_at = now()")
    vals.append(rel_id)
    row = await pool.fetchrow(
        f"UPDATE relationships SET {', '.join(sets)} WHERE id = ${idx} RETURNING *", *vals
    )
    return Relationship.from_row(row) if row else None


async def delete_relationship(pool: asyncpg.Pool, rel_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM relationships WHERE id = $1", rel_id)
    return result == "DELETE 1"


def _asset_from_prefixed(row, prefix: str) -> Asset:
    from grcen.models.asset import AssetStatus, AssetType

    return Asset(
        id=row[f"{prefix}id"],
        type=AssetType(row[f"{prefix}type"]),
        name=row[f"{prefix}name"],
        description=row[f"{prefix}description"],
        status=AssetStatus(row[f"{prefix}status"]),
        owner=row[f"{prefix}owner"],
        metadata_=row[f"{prefix}metadata"],
        created_at=row[f"{prefix}created_at"],
        updated_at=row[f"{prefix}updated_at"],
    )
