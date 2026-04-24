"""Saved searches — bookmarked URL (path + query string), per-user with optional sharing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg


@dataclass
class SavedSearch:
    id: UUID
    user_id: UUID
    name: str
    path: str
    query_string: str
    shared: bool
    created_at: datetime
    owner_username: str | None = None

    @property
    def href(self) -> str:
        if self.query_string:
            return f"{self.path}?{self.query_string}"
        return self.path

    @classmethod
    def from_row(cls, row) -> "SavedSearch":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            path=row["path"],
            query_string=row["query_string"] or "",
            shared=row["shared"],
            created_at=row["created_at"],
            owner_username=row.get("owner_username"),
        )


async def create_saved_search(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    name: str,
    path: str,
    query_string: str = "",
    shared: bool = False,
) -> SavedSearch:
    row = await pool.fetchrow(
        """INSERT INTO saved_searches
               (id, user_id, name, path, query_string, shared)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *""",
        uuid.uuid4(),
        user_id,
        name,
        path,
        query_string or "",
        shared,
    )
    return SavedSearch.from_row(row)


async def list_visible(
    pool: asyncpg.Pool, user_id: UUID, *, path: str | None = None
) -> list[SavedSearch]:
    """Return saved searches the user can see: their own + shared entries.

    Optionally filter to a single ``path`` so a page can render just its own
    bookmarks.
    """
    clauses = ["(s.user_id = $1 OR s.shared = true)"]
    vals: list = [user_id]
    if path:
        clauses.append(f"s.path = ${len(vals) + 1}")
        vals.append(path)
    where = " AND ".join(clauses)
    rows = await pool.fetch(
        f"""SELECT s.*, u.username AS owner_username
            FROM saved_searches s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE {where}
            ORDER BY s.shared DESC NULLS LAST, s.name""",
        *vals,
    )
    return [SavedSearch.from_row(r) for r in rows]


async def get_saved_search(
    pool: asyncpg.Pool, search_id: UUID
) -> SavedSearch | None:
    row = await pool.fetchrow("SELECT * FROM saved_searches WHERE id = $1", search_id)
    return SavedSearch.from_row(row) if row else None


async def delete_saved_search(
    pool: asyncpg.Pool, search_id: UUID, user_id: UUID, is_admin: bool = False
) -> bool:
    """Delete a saved search. Owner always may; admins may delete anyone's.

    Returns True if a row was removed.
    """
    if is_admin:
        result = await pool.execute(
            "DELETE FROM saved_searches WHERE id = $1", search_id
        )
    else:
        result = await pool.execute(
            "DELETE FROM saved_searches WHERE id = $1 AND user_id = $2",
            search_id,
            user_id,
        )
    return result == "DELETE 1"
