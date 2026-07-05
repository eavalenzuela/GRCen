"""Generic external-source upsert primitive.

Every authoritative external system that owns a slice of the asset graph — an
HRIS owning People, a CMDB owning Systems, an IdP (via SCIM) owning People, a
vuln tracker owning Findings — projects its records into GRCen assets and
relationships tagged with a per-source ``source`` string and a stable
``source_ref`` (the source system's own primary key). Re-projecting upserts in
place on ``(organization_id, source, source_ref)`` rather than duplicating, and
records the source no longer emits get pruned. This module is the reusable
substrate for that pattern; new connectors build on it instead of re-deriving
the upsert SQL.

Relationship to ``services/catalog_sync`` (which predates this): a *catalog
projection* deliberately lets the source own name/description/metadata but NOT
lifecycle ``status`` — so a human archiving a synced control isn't silently
un-archived on the next sync. An *authoritative system-of-record* connector, by
contrast, owns the whole record including ``status`` (that's exactly how SCIM
deprovisioning archives a Person). So ``upsert_external_asset`` writes ``status``
on both insert and update — a different, intentional semantic from catalog_sync,
which is why the two aren't collapsed into one call.

Both an ``asyncpg.Pool`` and a single acquired ``Connection`` satisfy the
``conn`` parameter (they share the fetchrow/fetch/execute surface). Pass a
connection to enrol several upserts + a prune in one transaction — the shape a
bulk connector wants; pass the pool for a one-shot upsert — the shape SCIM's
per-user provisioning wants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg

# asyncpg's Pool and Connection expose the same fetchrow/fetch/execute surface.
Conn = asyncpg.Pool | asyncpg.Connection


@dataclass
class UpsertResult:
    id: UUID
    inserted: bool  # True on INSERT, False on in-place UPDATE


_ASSET_UPSERT = """
    INSERT INTO assets (type, name, description, status, metadata,
                        organization_id, source, source_ref)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (organization_id, source, source_ref) WHERE source IS NOT NULL
    DO UPDATE SET name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  status = EXCLUDED.status,
                  metadata = EXCLUDED.metadata,
                  updated_at = now()
    RETURNING id, (xmax = 0) AS inserted
"""

_EDGE_UPSERT = """
    INSERT INTO relationships (source_asset_id, target_asset_id,
                              relationship_type, description,
                              organization_id, source, source_ref)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (organization_id, source, source_ref) WHERE source IS NOT NULL
    DO UPDATE SET source_asset_id = EXCLUDED.source_asset_id,
                  target_asset_id = EXCLUDED.target_asset_id,
                  relationship_type = EXCLUDED.relationship_type,
                  description = EXCLUDED.description,
                  updated_at = now()
    RETURNING id, (xmax = 0) AS inserted
"""


def _require(value: str, field: str) -> str:
    # A NULL/empty source would drop the row into the human-authored space
    # (source IS NULL) and defeat the whole upsert — fail loudly instead.
    if not value:
        raise ValueError(f"external upsert requires a non-empty {field}")
    return value


async def upsert_external_asset(
    conn: Conn,
    *,
    organization_id: UUID,
    source: str,
    source_ref: str,
    asset_type: str,
    name: str,
    description: str | None = None,
    status: str = "active",
    metadata: dict | None = None,
) -> UpsertResult:
    """Insert or update the asset owned by ``(source, source_ref)``.

    The source owns every projected field, ``status`` included (see module
    docstring). Idempotent: re-running with the same key updates in place.
    """
    _require(source, "source")
    _require(source_ref, "source_ref")
    row = await conn.fetchrow(
        _ASSET_UPSERT,
        asset_type,
        (name or "")[:255],
        description or "",
        status,
        json.dumps(metadata or {}),
        organization_id,
        source,
        source_ref,
    )
    return UpsertResult(id=row["id"], inserted=row["inserted"])


async def upsert_external_edge(
    conn: Conn,
    *,
    organization_id: UUID,
    source: str,
    source_ref: str,
    source_asset_id: UUID,
    target_asset_id: UUID,
    relationship_type: str,
    description: str = "",
) -> UpsertResult:
    """Insert or update the relationship owned by ``(source, source_ref)``."""
    _require(source, "source")
    _require(source_ref, "source_ref")
    row = await conn.fetchrow(
        _EDGE_UPSERT,
        source_asset_id,
        target_asset_id,
        relationship_type,
        description,
        organization_id,
        source,
        source_ref,
    )
    return UpsertResult(id=row["id"], inserted=row["inserted"])


async def prune_stale_assets(
    conn: Conn,
    *,
    organization_id: UUID,
    source: str,
    seen_refs: list[str],
    delete: bool = False,
) -> list[str]:
    """Return source_refs tagged ``source`` that weren't in ``seen_refs``.

    When ``delete`` is true they are removed. Deleting an asset cascades to any
    human-authored edges hanging off it, so callers gate deletion behind this
    flag (mirroring ``catalog_sync``'s stale-asset handling) — the default is to
    report only. Only ever touches rows carrying this exact ``source`` tag;
    human-authored (``source IS NULL``) and other-source rows are untouched.
    """
    _require(source, "source")
    stale = await conn.fetch(
        """SELECT source_ref FROM assets
           WHERE organization_id = $1 AND source = $2
             AND source_ref <> ALL($3::text[])""",
        organization_id, source, seen_refs,
    )
    refs = [r["source_ref"] for r in stale]
    if delete and refs:
        await conn.execute(
            """DELETE FROM assets
               WHERE organization_id = $1 AND source = $2
                 AND source_ref <> ALL($3::text[])""",
            organization_id, source, seen_refs,
        )
    return refs


async def prune_stale_edges(
    conn: Conn,
    *,
    organization_id: UUID,
    source: str,
    seen_refs: list[str],
) -> int:
    """Delete edges tagged ``source`` whose source_ref isn't in ``seen_refs``.

    Edges cascade to nothing, so unlike assets they're always safe to prune —
    no gating flag. Returns the count deleted.
    """
    _require(source, "source")
    result = await conn.execute(
        """DELETE FROM relationships
           WHERE organization_id = $1 AND source = $2
             AND source_ref <> ALL($3::text[])""",
        organization_id, source, seen_refs,
    )
    return int(result.split()[-1])
