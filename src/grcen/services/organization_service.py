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
    email_from_name: str = ""
    email_brand_color: str = ""
    email_logo_url: str = ""

    @classmethod
    def from_row(cls, row) -> "Organization":
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            created_at=row["created_at"],
            email_from_name=row.get("email_from_name") or "",
            email_brand_color=row.get("email_brand_color") or "",
            email_logo_url=row.get("email_logo_url") or "",
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


async def update_branding(
    pool: asyncpg.Pool,
    org_id: UUID,
    *,
    email_from_name: str = "",
    email_brand_color: str = "",
    email_logo_url: str = "",
) -> None:
    await pool.execute(
        """UPDATE organizations
              SET email_from_name = $2,
                  email_brand_color = $3,
                  email_logo_url = $4
            WHERE id = $1""",
        org_id, email_from_name, email_brand_color, email_logo_url,
    )


async def delete_organization(pool: asyncpg.Pool, org_id: UUID) -> bool:
    """Hard-delete an organization. Refuses to drop the seeded default org."""
    row = await pool.fetchrow(
        "SELECT slug FROM organizations WHERE id = $1", org_id
    )
    if not row:
        return False
    if row["slug"] == DEFAULT_SLUG:
        raise ValueError("Cannot delete the default organization.")
    result = await pool.execute("DELETE FROM organizations WHERE id = $1", org_id)
    return result == "DELETE 1"


async def list_memberships(pool: asyncpg.Pool, user_id: UUID) -> list[dict]:
    """Return every (org, role) pair the user is a member of."""
    rows = await pool.fetch(
        """SELECT m.organization_id AS id, o.slug, o.name, m.role, m.is_default
           FROM user_organizations m
           JOIN organizations o ON o.id = m.organization_id
           WHERE m.user_id = $1
           ORDER BY o.name""",
        user_id,
    )
    return [dict(r) for r in rows]


async def add_membership(
    pool: asyncpg.Pool,
    user_id: UUID,
    organization_id: UUID,
    role: str = "viewer",
    *,
    is_default: bool = False,
) -> None:
    await pool.execute(
        """INSERT INTO user_organizations (user_id, organization_id, role, is_default)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (user_id, organization_id) DO UPDATE SET role = EXCLUDED.role""",
        user_id, organization_id, role, is_default,
    )


async def remove_membership(
    pool: asyncpg.Pool, user_id: UUID, organization_id: UUID
) -> bool:
    result = await pool.execute(
        "DELETE FROM user_organizations WHERE user_id = $1 AND organization_id = $2",
        user_id, organization_id,
    )
    return result == "DELETE 1"


async def is_member(
    pool: asyncpg.Pool, user_id: UUID, organization_id: UUID
) -> tuple[bool, str | None]:
    """Return (is_member, role-in-that-org-or-None)."""
    row = await pool.fetchrow(
        "SELECT role FROM user_organizations WHERE user_id = $1 AND organization_id = $2",
        user_id, organization_id,
    )
    if row is None:
        return False, None
    return True, row["role"]


async def stats_for_orgs(pool: asyncpg.Pool) -> list[dict]:
    """Return per-org counts for the cross-org admin view."""
    rows = await pool.fetch(
        """SELECT o.id, o.slug, o.name, o.created_at,
                  (SELECT count(*) FROM users WHERE organization_id = o.id) AS user_count,
                  (SELECT count(*) FROM assets WHERE organization_id = o.id) AS asset_count
           FROM organizations o
           ORDER BY o.name"""
    )
    return [dict(r) for r in rows]
