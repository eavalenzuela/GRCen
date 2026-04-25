"""Cross-cutting tag vocabulary operations.

Tags are stored as a ``TEXT[]`` column on ``assets`` with a GIN index
(``idx_assets_tags``).  This module is a thin aggregation + admin layer
around that column.  We deliberately avoid a join table — the array column
with a GIN index is cheaper for the read patterns we care about (list all
distinct tags with counts, filter assets by tag) and the admin ops
(rename, delete) are rare.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg


@dataclass
class TagCount:
    name: str
    asset_count: int


async def list_tags_with_counts(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[TagCount]:
    """Return every distinct tag with the number of assets using it."""
    rows = await pool.fetch(
        """SELECT unnest(tags) AS tag, count(*)::int AS n
           FROM assets
           WHERE tags IS NOT NULL AND array_length(tags, 1) > 0
             AND ($1::uuid IS NULL OR organization_id = $1)
           GROUP BY tag
           ORDER BY n DESC, tag""",
        organization_id,
    )
    return [TagCount(name=r["tag"], asset_count=r["n"]) for r in rows]


async def rename_tag(
    pool: asyncpg.Pool, old: str, new: str, *, organization_id: UUID | None = None
) -> int:
    """Replace every occurrence of ``old`` with ``new``. Returns affected row count.

    Deduplicates on the way through: if an asset already has ``new``,
    ``old`` is simply removed rather than producing a duplicate entry.
    """
    old = old.strip()
    new = new.strip()
    if not old or not new or old == new:
        return 0
    result = await pool.execute(
        """UPDATE assets
             SET tags = (
                 SELECT array_agg(DISTINCT t)
                 FROM unnest(
                     array_replace(tags, $1, $2)
                 ) AS t
             ),
                 updated_at = now()
           WHERE $1 = ANY(tags)
             AND ($3::uuid IS NULL OR organization_id = $3)""",
        old,
        new,
        organization_id,
    )
    return _affected(result)


async def delete_tag(
    pool: asyncpg.Pool, name: str, *, organization_id: UUID | None = None
) -> int:
    """Remove ``name`` from every asset that has it within the org."""
    name = name.strip()
    if not name:
        return 0
    result = await pool.execute(
        """UPDATE assets
             SET tags = array_remove(tags, $1),
                 updated_at = now()
           WHERE $1 = ANY(tags)
             AND ($2::uuid IS NULL OR organization_id = $2)""",
        name,
        organization_id,
    )
    return _affected(result)


def _affected(status: str) -> int:
    # asyncpg returns e.g. "UPDATE 5"
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0
