import json
import uuid
from uuid import UUID

import asyncpg

from grcen.models.asset import Asset, AssetType


async def create_asset(
    pool: asyncpg.Pool,
    *,
    type: AssetType,
    name: str,
    description: str | None = None,
    status: str = "active",
    owner: str | None = None,
    metadata_: dict | None = None,
    updated_by: UUID | None = None,
) -> Asset:
    row = await pool.fetchrow(
        """
        INSERT INTO assets (id, type, name, description, status, owner, metadata, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        uuid.uuid4(),
        type.value,
        name,
        description,
        status,
        owner,
        json.dumps(metadata_ or {}),
        updated_by,
    )
    return Asset.from_row(row)


async def get_asset(pool: asyncpg.Pool, asset_id: UUID) -> Asset | None:
    row = await pool.fetchrow("SELECT * FROM assets WHERE id = $1", asset_id)
    return Asset.from_row(row) if row else None


async def list_assets(
    pool: asyncpg.Pool,
    asset_type: AssetType | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Asset], int]:
    offset = (page - 1) * page_size
    if asset_type:
        rows = await pool.fetch(
            "SELECT * FROM assets WHERE type = $1 ORDER BY name LIMIT $2 OFFSET $3",
            asset_type.value,
            page_size,
            offset,
        )
        total = await pool.fetchval(
            "SELECT count(*) FROM assets WHERE type = $1", asset_type.value
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM assets ORDER BY name LIMIT $1 OFFSET $2", page_size, offset
        )
        total = await pool.fetchval("SELECT count(*) FROM assets")
    return [Asset.from_row(r) for r in rows], total


async def update_asset(
    pool: asyncpg.Pool,
    asset_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    metadata_: dict | None = None,
    updated_by: UUID | None = None,
) -> Asset | None:
    # Build SET clause dynamically from provided fields
    sets: list[str] = []
    vals: list = []
    idx = 1

    for col, val in [
        ("name", name),
        ("description", description),
        ("status", status),
        ("owner", owner),
    ]:
        if val is not None:
            sets.append(f"{col} = ${idx}")
            vals.append(val)
            idx += 1
    if metadata_ is not None:
        sets.append(f"metadata = ${idx}")
        vals.append(json.dumps(metadata_))
        idx += 1

    if updated_by is not None:
        sets.append(f"updated_by = ${idx}")
        vals.append(updated_by)
        idx += 1

    if not sets:
        return await get_asset(pool, asset_id)

    sets.append("updated_at = now()")
    vals.append(asset_id)
    query = f"UPDATE assets SET {', '.join(sets)} WHERE id = ${idx} RETURNING *"
    row = await pool.fetchrow(query, *vals)
    return Asset.from_row(row) if row else None


async def delete_asset(pool: asyncpg.Pool, asset_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM assets WHERE id = $1", asset_id)
    return result == "DELETE 1"


async def search_assets(
    pool: asyncpg.Pool, query_str: str, limit: int = 20
) -> list[Asset]:
    rows = await pool.fetch(
        "SELECT * FROM assets WHERE name ILIKE $1 ORDER BY name LIMIT $2",
        f"%{query_str}%",
        limit,
    )
    return [Asset.from_row(r) for r in rows]
