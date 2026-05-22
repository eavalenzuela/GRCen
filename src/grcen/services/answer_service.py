"""Answer-library service (feature_roadmap.md #21).

Answer-library entries are `AssetType.ANSWER` assets: the question is the asset
``name``, the canonical answer is the asset ``description``, and structured
metadata (short answer, review date) lives in custom fields. Each answer links
to the Control/Policy/System/Framework/Audit assets that substantiate it via
``substantiated_by`` relationships — those links are what later phases use to
detect stale answers.
"""
import json
from typing import Any
from uuid import UUID

import asyncpg

from grcen.models.asset import AssetType

SUBSTANTIATES_REL = "substantiated_by"


def _as_dict(metadata: Any) -> dict:
    if isinstance(metadata, str):
        try:
            return json.loads(metadata)
        except (ValueError, TypeError):
            return {}
    return metadata or {}


async def list_answers(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Every Answer asset with the assets that substantiate it.

    Returns one dict per answer: id, question, answer, short_answer,
    last_reviewed, status, and a ``substantiators`` list of {id, name, type}.
    """
    rows = await pool.fetch(
        """SELECT a.id, a.name, a.description, a.status, a.metadata, a.updated_at,
                  s.id AS sub_id, s.name AS sub_name, s.type::text AS sub_type
           FROM assets a
           LEFT JOIN relationships r
             ON r.source_asset_id = a.id AND r.relationship_type = $2
           LEFT JOIN assets s ON s.id = r.target_asset_id
           WHERE a.type = $1
             AND ($3::uuid IS NULL OR a.organization_id = $3)
           ORDER BY a.name, s.name""",
        AssetType.ANSWER.value,
        SUBSTANTIATES_REL,
        organization_id,
    )

    by_answer: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        entry = by_answer.get(r["id"])
        if entry is None:
            meta = _as_dict(r["metadata"])
            entry = {
                "id": r["id"],
                "question": r["name"],
                "answer": r["description"],
                "short_answer": meta.get("short_answer"),
                "answer_format": meta.get("answer_format"),
                "last_reviewed": meta.get("last_reviewed"),
                "status": r["status"],
                "updated_at": r["updated_at"],
                "substantiators": [],
            }
            by_answer[r["id"]] = entry
        if r["sub_id"] is not None:
            entry["substantiators"].append(
                {"id": r["sub_id"], "name": r["sub_name"], "type": r["sub_type"]}
            )
    return list(by_answer.values())


async def count_answers(pool: asyncpg.Pool, *, organization_id: UUID | None = None) -> int:
    return await pool.fetchval(
        """SELECT count(*) FROM assets
           WHERE type = $1 AND ($2::uuid IS NULL OR organization_id = $2)""",
        AssetType.ANSWER.value,
        organization_id,
    )
