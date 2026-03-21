import json
import uuid
from datetime import date, datetime, timedelta
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
    asset_types: list[AssetType] | None = None,
    page: int = 1,
    page_size: int = 25,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    metadata_filters: dict[str, str] | None = None,
) -> tuple[list[Asset], int]:
    where_parts: list[str] = []
    vals: list = []
    idx = 1

    # Single type (backwards compat) or multi-type
    if asset_type:
        where_parts.append(f"type = ${idx}")
        vals.append(asset_type.value)
        idx += 1
    elif asset_types:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(asset_types)))
        where_parts.append(f"type IN ({placeholders})")
        for at in asset_types:
            vals.append(at.value)
            idx += 1

    if q:
        where_parts.append(f"(name ILIKE ${idx} OR description ILIKE ${idx} OR owner ILIKE ${idx})")
        vals.append(f"%{q}%")
        idx += 1

    if status:
        where_parts.append(f"status = ${idx}")
        vals.append(status)
        idx += 1

    if owner:
        where_parts.append(f"owner ILIKE ${idx}")
        vals.append(f"%{owner}%")
        idx += 1

    if created_after:
        where_parts.append(f"created_at >= ${idx}")
        vals.append(date.fromisoformat(created_after))
        idx += 1

    if created_before:
        where_parts.append(f"created_at < ${idx}")
        vals.append(date.fromisoformat(created_before) + timedelta(days=1))
        idx += 1

    if metadata_filters:
        for key, value in metadata_filters.items():
            where_parts.append(f"metadata->>'{key}' = ${idx}")
            vals.append(value)
            idx += 1

    where_clause = " AND ".join(where_parts) if where_parts else "TRUE"

    total = await pool.fetchval(
        f"SELECT count(*) FROM assets WHERE {where_clause}", *vals
    )

    vals.append(page_size)
    vals.append((page - 1) * page_size)
    rows = await pool.fetch(
        f"SELECT * FROM assets WHERE {where_clause} ORDER BY name LIMIT ${idx} OFFSET ${idx + 1}",
        *vals,
    )
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
