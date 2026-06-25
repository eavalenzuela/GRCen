import json
import uuid
from datetime import date, timedelta
from uuid import UUID

import asyncpg

from grcen.custom_fields import CUSTOM_FIELDS
from grcen.models.asset import POSTURE_TYPES, Asset, AssetType

# Custom-field names flagged sensitive in any asset type. Free-text search
# excludes these metadata values so it can't become a PII side channel (a user
# without VIEW_PII shouldn't be able to confirm e.g. an email/SSN by probing).
SENSITIVE_FIELD_NAMES: list[str] = sorted(
    {f.name for fields in CUSTOM_FIELDS.values() for f in fields if f.sensitive}
)

_SELECT_WITH_OWNER = """
    SELECT a.*, o.name AS owner_name
    FROM assets a
    LEFT JOIN assets o ON o.id = a.owner_id
"""


async def create_asset(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    type: AssetType,
    name: str,
    description: str | None = None,
    status: str = "active",
    owner_id: UUID | None = None,
    metadata_: dict | None = None,
    updated_by: UUID | None = None,
    tags: list[str] | None = None,
    criticality: str | None = None,
) -> Asset:
    if organization_id is None:
        from grcen.services import organization_service
        organization_id = await organization_service.get_default_org_id(pool)
    if owner_id is not None:
        owner_row = await pool.fetchrow(
            "SELECT name, organization_id FROM assets WHERE id = $1", owner_id
        )
        if owner_row is None or owner_row["organization_id"] != organization_id:
            raise ValueError("owner_id refers to an asset in a different organization")
    row = await pool.fetchrow(
        """
        INSERT INTO assets (id, type, name, description, status, owner_id, metadata, updated_by, tags, criticality, organization_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
        tags or [],
        criticality,
        organization_id,
    )
    if owner_id:
        row_dict = dict(row)
        row_dict["owner_name"] = owner_row["name"]
        return Asset.from_row(row_dict)
    return Asset.from_row(row)


async def get_asset(
    pool: asyncpg.Pool, asset_id: UUID, *, organization_id: UUID | None = None
) -> Asset | None:
    if organization_id is not None:
        row = await pool.fetchrow(
            _SELECT_WITH_OWNER + " WHERE a.id = $1 AND a.organization_id = $2",
            asset_id,
            organization_id,
        )
    else:
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
    tag: str | None = None,
    unlinked: bool = False,
    sort: str = "name",
    order: str = "asc",
    organization_id: UUID | None = None,
) -> tuple[list[Asset], int]:
    where_parts: list[str] = []
    vals: list = []
    idx = 1
    if organization_id is not None:
        where_parts.append(f"a.organization_id = ${idx}")
        vals.append(organization_id)
        idx += 1

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
    else:
        # No explicit type filter: hide posture/metadata types (e.g. answer
        # library entries) from the general listing. They have their own
        # surfaces; an explicit ?type=answer filter still reaches them.
        posture = [t.value for t in POSTURE_TYPES]
        if posture:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(posture)))
            where_parts.append(f"a.type NOT IN ({placeholders})")
            for pv in posture:
                vals.append(pv)
                idx += 1

    if q:
        qph = idx
        vals.append(f"%{q}%")
        idx += 1
        sens_ph = idx
        vals.append(SENSITIVE_FIELD_NAMES)
        idx += 1
        where_parts.append(
            f"(a.name ILIKE ${qph} OR a.description ILIKE ${qph} OR o.name ILIKE ${qph}"
            # non-sensitive custom-field values
            f" OR EXISTS (SELECT 1 FROM jsonb_each_text(COALESCE(a.metadata, '{{}}'::jsonb)) kv"
            f"            WHERE kv.value ILIKE ${qph} AND kv.key <> ALL(${sens_ph}::text[]))"
            # free-text notes on relationships touching this asset
            f" OR EXISTS (SELECT 1 FROM relationships r"
            f"            WHERE (r.source_asset_id = a.id OR r.target_asset_id = a.id)"
            f"              AND r.description ILIKE ${qph}))"
        )

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

    if tag:
        where_parts.append(f"${idx} = ANY(a.tags)")
        vals.append(tag)
        idx += 1

    if unlinked:
        # Assets with no relationship on either end — the disconnected nodes a
        # user most needs to find and wire up. Parameterless (matches on a.id).
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM relationships r "
            "WHERE r.source_asset_id = a.id OR r.target_asset_id = a.id)"
        )

    if metadata_filters:
        import re
        for key, value in metadata_filters.items():
            # Validate key is a safe identifier to prevent SQL injection
            if not re.match(r"^[a-zA-Z0-9_\-]+$", key):
                continue
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
        "updated_at": "a.updated_at",
    }
    sort_col = allowed_sorts.get(sort, "a.name")
    # Sort by a custom field: "meta.<key>" → a.metadata->>'<key>'. Integer custom
    # fields (e.g. headcount, open_findings) sort numerically via a per-value cast
    # so a single non-numeric row can't abort the whole ORDER BY; everything else
    # sorts as text.
    if sort.startswith("meta."):
        import re
        meta_sort_key = sort[len("meta."):]
        if re.match(r"^[a-zA-Z0-9_\-]+$", meta_sort_key):
            numeric = False
            if asset_type is not None:
                fd = next(
                    (f for f in CUSTOM_FIELDS.get(asset_type, []) if f.name == meta_sort_key),
                    None,
                )
                numeric = fd is not None and fd.field_type == "integer"
            if numeric:
                sort_col = (
                    f"(CASE WHEN a.metadata->>'{meta_sort_key}' ~ '^-?[0-9.]+$' "
                    f"THEN (a.metadata->>'{meta_sort_key}')::numeric ELSE NULL END)"
                )
            else:
                sort_col = f"a.metadata->>'{meta_sort_key}'"
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
    organization_id: UUID | None = None,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    owner_id: UUID | None = None,
    metadata_: dict | None = None,
    updated_by: UUID | None = None,
    tags: list[str] | None = None,
    criticality: str | None = None,
) -> Asset | None:
    if organization_id is not None:
        existing = await pool.fetchrow(
            "SELECT organization_id FROM assets WHERE id = $1", asset_id
        )
        if existing is None or existing["organization_id"] != organization_id:
            return None
    if owner_id is not None and organization_id is not None:
        owner_row = await pool.fetchrow(
            "SELECT organization_id FROM assets WHERE id = $1", owner_id
        )
        if owner_row is None or owner_row["organization_id"] != organization_id:
            raise ValueError("owner_id refers to an asset in a different organization")
    # Build SET clause dynamically from provided fields
    sets: list[str] = []
    vals: list = []
    idx = 1

    for col, val in [
        ("name", name),
        ("description", description),
        ("status", status),
        ("criticality", criticality),
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

    if tags is not None:
        sets.append(f"tags = ${idx}")
        vals.append(tags)
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


async def bulk_update_assets(
    pool: asyncpg.Pool,
    asset_ids: list[UUID],
    *,
    asset_type: AssetType,
    status: str | None = None,
    owner_id: UUID | None = None,
    add_tags: list[str] | None = None,
    metadata_set: dict | None = None,
    updated_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> list[UUID]:
    """Apply a uniform set of changes to many assets of one type (direct path).

    Generalizes ``risk_service.bulk_update_risks``: ``metadata_set`` merges into
    each asset's existing metadata (other keys kept), ``add_tags`` appends and
    de-dups, ``status``/``owner_id`` are column updates. The ``WHERE`` is pinned
    to both ``organization_id`` and ``asset_type`` so a bulk can only ever touch
    the intended type and tenant. Returns the ids actually changed.
    """
    if not asset_ids:
        return []
    if not status and owner_id is None and not add_tags and not metadata_set:
        return []
    # Cross-tenant owner guard (mirrors update_asset).
    if owner_id is not None and organization_id is not None:
        ok = await pool.fetchval(
            "SELECT 1 FROM assets WHERE id = $1 AND organization_id = $2",
            owner_id, organization_id,
        )
        if not ok:
            raise ValueError("owner_id refers to an asset in a different organization")

    updated: list[UUID] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for aid in asset_ids:
                if organization_id is not None:
                    row = await conn.fetchrow(
                        "SELECT metadata, tags FROM assets"
                        " WHERE id = $1 AND type = $2 AND organization_id = $3",
                        aid, asset_type.value, organization_id,
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT metadata, tags FROM assets WHERE id = $1 AND type = $2",
                        aid, asset_type.value,
                    )
                if not row:
                    continue
                sets: list[str] = []
                vals: list = []
                idx = 1
                if status:
                    sets.append(f"status = ${idx}"); vals.append(status); idx += 1
                if owner_id is not None:
                    sets.append(f"owner_id = ${idx}"); vals.append(owner_id); idx += 1
                if metadata_set:
                    meta = row["metadata"]
                    if isinstance(meta, str):
                        meta = json.loads(meta) or {}
                    elif meta is None:
                        meta = {}
                    else:
                        meta = dict(meta)
                    meta.update(metadata_set)
                    sets.append(f"metadata = ${idx}::jsonb"); vals.append(json.dumps(meta)); idx += 1
                if add_tags:
                    existing = list(row["tags"]) if row["tags"] else []
                    merged = list(dict.fromkeys(existing + add_tags))
                    sets.append(f"tags = ${idx}"); vals.append(merged); idx += 1
                if not sets:
                    continue
                sets.append("updated_at = now()")
                if updated_by is not None:
                    sets.append(f"updated_by = ${idx}"); vals.append(updated_by); idx += 1
                vals.append(aid)
                await conn.execute(
                    f"UPDATE assets SET {', '.join(sets)} WHERE id = ${idx}", *vals
                )
                updated.append(aid)
    return updated


async def delete_asset(
    pool: asyncpg.Pool, asset_id: UUID, *, organization_id: UUID | None = None
) -> bool:
    if organization_id is not None:
        result = await pool.execute(
            "DELETE FROM assets WHERE id = $1 AND organization_id = $2",
            asset_id, organization_id,
        )
    else:
        result = await pool.execute("DELETE FROM assets WHERE id = $1", asset_id)
    return result == "DELETE 1"


async def clone_asset(
    pool: asyncpg.Pool,
    asset_id: UUID,
    *,
    organization_id: UUID | None = None,
    new_name: str | None = None,
    clone_relationships: bool = False,
    updated_by: UUID | None = None,
) -> Asset | None:
    """Clone an asset, optionally including its relationships."""
    if organization_id is None:
        from grcen.services import organization_service
        organization_id = await organization_service.get_default_org_id(pool)
    original = await get_asset(pool, asset_id, organization_id=organization_id)
    if not original:
        return None
    name = new_name or f"{original.name} (Copy)"
    new_id = uuid.uuid4()
    row = await pool.fetchrow(
        """
        INSERT INTO assets (id, type, name, description, status, owner_id, metadata, updated_by, tags, criticality, organization_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
        original.tags or [],
        original.criticality,
        organization_id,
    )
    if clone_relationships:
        rels = await pool.fetch(
            "SELECT * FROM relationships WHERE source_asset_id = $1",
            asset_id,
        )
        for rel in rels:
            await pool.execute(
                """INSERT INTO relationships (id, source_asset_id, target_asset_id, relationship_type, description, organization_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uuid.uuid4(), new_id, rel["target_asset_id"],
                rel["relationship_type"], rel["description"], organization_id,
            )
        rels = await pool.fetch(
            "SELECT * FROM relationships WHERE target_asset_id = $1",
            asset_id,
        )
        for rel in rels:
            await pool.execute(
                """INSERT INTO relationships (id, source_asset_id, target_asset_id, relationship_type, description, organization_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uuid.uuid4(), rel["source_asset_id"], new_id,
                rel["relationship_type"], rel["description"], organization_id,
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
    organization_id: UUID | None = None,
) -> list[Asset]:
    # $1 = substring pattern (indexable via the name/description trigram GINs),
    # $2 = raw query for fuzzy matching. `$2 <% a.name` is pg_trgm word-similarity
    # (typo tolerance, also index-accelerated). Results are ranked: exact-substring
    # name hits first, then by descending similarity — relevance, not alphabetical.
    like = f"%{query_str}%"
    match = "(a.name ILIKE $1 OR a.description ILIKE $1 OR $2 <% a.name)"
    order = "ORDER BY (a.name ILIKE $1) DESC, word_similarity($2, a.name) DESC, a.name"
    if types:
        placeholders = ", ".join(f"${i}" for i in range(5, 5 + len(types)))
        rows = await pool.fetch(
            f"""{_SELECT_WITH_OWNER}
                WHERE {match} AND a.type IN ({placeholders})
                  AND ($3::uuid IS NULL OR a.organization_id = $3)
                {order} LIMIT $4""",
            like,
            query_str,
            organization_id,
            limit,
            *[t.value for t in types],
        )
    else:
        rows = await pool.fetch(
            f"""{_SELECT_WITH_OWNER}
                WHERE {match}
                  AND ($3::uuid IS NULL OR a.organization_id = $3)
                {order} LIMIT $4""",
            like,
            query_str,
            organization_id,
            limit,
        )
    return [Asset.from_row(r) for r in rows]
