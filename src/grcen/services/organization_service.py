"""Organization (tenant) service."""
import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg


@dataclass
class Organization:
    id: UUID
    slug: str
    name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "Organization":
        return cls(
            id=row["id"], slug=row["slug"], name=row["name"], created_at=row["created_at"]
        )


DEFAULT_SLUG = "default"


async def get_default_org_id(pool: asyncpg.Pool) -> UUID:
    row = await pool.fetchrow(
        "SELECT id FROM organizations WHERE slug = $1", DEFAULT_SLUG
    )
    if row is None:
        # Should never happen — schema migration seeds it.
        raise RuntimeError("Default organization missing from database.")
    return row["id"]


async def get_by_id(pool: asyncpg.Pool, org_id: UUID) -> Organization | None:
    row = await pool.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    return Organization.from_row(row) if row else None


async def get_by_slug(pool: asyncpg.Pool, slug: str) -> Organization | None:
    row = await pool.fetchrow("SELECT * FROM organizations WHERE slug = $1", slug)
    return Organization.from_row(row) if row else None


async def list_organizations(pool: asyncpg.Pool) -> list[Organization]:
    rows = await pool.fetch("SELECT * FROM organizations ORDER BY name")
    return [Organization.from_row(r) for r in rows]


async def create_organization(pool: asyncpg.Pool, *, slug: str, name: str) -> Organization:
    row = await pool.fetchrow(
        """INSERT INTO organizations (id, slug, name) VALUES ($1, $2, $3) RETURNING *""",
        uuid.uuid4(), slug, name,
    )
    return Organization.from_row(row)
