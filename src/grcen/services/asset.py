import json
import uuid
from datetime import date, datetime, timedelta
from uuid import UUID

import asyncpg

from grcen.models.asset import Asset, AssetType

_SELECT_WITH_OWNER = """
    SELECT a.*, o.name AS owner_name
    FROM assets a
    LEFT JOIN assets o ON o.id = a.owner_id
"""


async def create_asset(
    pool: asyncpg.Pool,
    *,
    type: AssetType,
    name: str,
    description: str | None = None,
    status: str = "active",
    owner_id: UUID | None = None,
    metadata_: dict | None = None,
    updated_by: UUID | None = None,
) -> Asset:
    row = await pool.fetchrow(
        """
        INSERT INTO assets (id, type, name, description, status, owner_id, metadata, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        uuid.uuid4(),
        type.value,
        name,
        description,
        status,
        owner_id,
        json.dumps(metadata_ or {}),
        updated_by,
    )
    # Resolve owner name
    if owner_id:
        owner_row = await pool.fetchrow("SELECT name FROM assets WHERE id = $1", owner_id)
        # Build a dict-like row with owner_name
        row_dict = dict(row)
        row_dict["owner_name"] = owner_row["name"] if owner_row else None
        return Asset.from_row(row_dict)
    return Asset.from_row(row)


async def get_asset(pool: asyncpg.Pool, asset_id: UUID) -> Asset | None:
    row = await pool.fetchrow(
        _SELECT_WITH_OWNER + " WHERE a.id = $1",
        asset_id,
    )
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
    sort: str = "name",
    order: str = "asc",
) -> tuple[list[Asset], int]:
    where_parts: list[str] = []
    vals: list = []
    idx = 1

    # Single type (backwards compat) or multi-type
    if asset_type:
        where_parts.append(f"a.type = ${idx}")
        vals.append(asset_type.value)
        idx += 1
    elif asset_types:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(asset_types)))
        where_parts.append(f"a.type IN ({placeholders})")
        for at in asset_types:
            vals.append(at.value)
            idx += 1

    if q:
        where_parts.append(f"(a.name ILIKE ${idx} OR a.description ILIKE ${idx} OR o.name ILIKE ${idx})")
        vals.append(f"%{q}%")
        idx += 1

    if status:
        where_parts.append(f"a.status = ${idx}")
        vals.append(status)
        idx += 1

    if owner:
        where_parts.append(f"o.name ILIKE ${idx}")
        vals.append(f"%{owner}%")
        idx += 1

    if created_after:
        where_parts.append(f"a.created_at >= ${idx}")
        vals.append(date.fromisoformat(created_after))
        idx += 1

    if created_before:
        where_parts.append(f"a.created_at < ${idx}")
        vals.append(date.fromisoformat(created_before) + timedelta(days=1))
        idx += 1

    if metadata_filters:
        for key, value in metadata_filters.items():
            where_parts.append(f"a.metadata->>'{key}' = ${idx}")
            vals.append(value)
            idx += 1

    where_clause = " AND ".join(where_parts) if where_parts else "TRUE"

    total = await pool.fetchval(
        f"""SELECT count(*) FROM assets a
            LEFT JOIN assets o ON o.id = a.owner_id
            WHERE {where_clause}""",
        *vals,
    )

    # Validate sort column to prevent injection
    allowed_sorts = {
        "name": "a.name",
        "type": "a.type",
        "status": "a.status",
        "owner": "o.name",
        "created_at": "a.created_at",
    }
    sort_col = allowed_sorts.get(sort, "a.name")
    sort_dir = "DESC" if order == "desc" else "ASC"

    vals.append(page_size)
    vals.append((page - 1) * page_size)
    rows = await pool.fetch(
        f"""{_SELECT_WITH_OWNER}
            WHERE {where_clause}
            ORDER BY {sort_col} {sort_dir} NULLS LAST
            LIMIT ${idx} OFFSET ${idx + 1}""",
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
    owner_id: UUID | None = None,
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
    ]:
        if val is not None:
            sets.append(f"{col} = ${idx}")
            vals.append(val)
            idx += 1

    if owner_id is not None:
        sets.append(f"owner_id = ${idx}")
        vals.append(owner_id)
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
    if not row:
        return None
    # Resolve owner name
    row_dict = dict(row)
    if row_dict.get("owner_id"):
        owner_row = await pool.fetchrow("SELECT name FROM assets WHERE id = $1", row_dict["owner_id"])
        row_dict["owner_name"] = owner_row["name"] if owner_row else None
    return Asset.from_row(row_dict)


async def delete_asset(pool: asyncpg.Pool, asset_id: UUID) -> bool:
    result = await pool.execute("DELETE FROM assets WHERE id = $1", asset_id)
    return result == "DELETE 1"


async def clone_asset(
    pool: asyncpg.Pool,
    asset_id: UUID,
    *,
    new_name: str | None = None,
    clone_relationships: bool = False,
    updated_by: UUID | None = None,
) -> Asset | None:
    """Clone an asset, optionally including its relationships."""
    original = await get_asset(pool, asset_id)
    if not original:
        return None
    name = new_name or f"{original.name} (Copy)"
    new_id = uuid.uuid4()
    row = await pool.fetchrow(
        """
        INSERT INTO assets (id, type, name, description, status, owner_id, metadata, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        new_id,
        original.type.value,
        name,
        original.description,
        original.status.value,
        original.owner_id,
        json.dumps(original.metadata_ or {}),
        updated_by,
    )
    if clone_relationships:
        # Clone relationships where the original is source
        rels = await pool.fetch(
            "SELECT * FROM relationships WHERE source_asset_id = $1",
            asset_id,
        )
        for rel in rels:
            await pool.execute(
                """INSERT INTO relationships (id, source_asset_id, target_asset_id, relationship_type, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                uuid.uuid4(), new_id, rel["target_asset_id"],
                rel["relationship_type"], rel["description"],
            )
        # Clone relationships where the original is target
        rels = await pool.fetch(
            "SELECT * FROM relationships WHERE target_asset_id = $1",
            asset_id,
        )
        for rel in rels:
            await pool.execute(
                """INSERT INTO relationships (id, source_asset_id, target_asset_id, relationship_type, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                uuid.uuid4(), rel["source_asset_id"], new_id,
                rel["relationship_type"], rel["description"],
            )
    # Resolve owner name for return
    row_dict = dict(row)
    if original.owner_id:
        row_dict["owner_name"] = original.owner
    return Asset.from_row(row_dict)


async def search_assets(
    pool: asyncpg.Pool,
    query_str: str,
    limit: int = 20,
    types: list[AssetType] | None = None,
) -> list[Asset]:
    if types:
        placeholders = ", ".join(f"${i}" for i in range(3, 3 + len(types)))
        rows = await pool.fetch(
            f"""{_SELECT_WITH_OWNER}
                WHERE a.name ILIKE $1 AND a.type IN ({placeholders})
                ORDER BY a.name LIMIT $2""",
            f"%{query_str}%",
            limit,
            *[t.value for t in types],
        )
    else:
        rows = await pool.fetch(
            _SELECT_WITH_OWNER + " WHERE a.name ILIKE $1 ORDER BY a.name LIMIT $2",
            f"%{query_str}%",
            limit,
        )
    return [Asset.from_row(r) for r in rows]
