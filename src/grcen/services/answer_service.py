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


# Substantiator states that make an answer's claim suspect. Because GRCen owns
# control/posture state, this degradation is detectable here without any
# external signal (feature_roadmap.md #21).
_DEGRADED_STATUS = {"inactive", "archived"}
_BAD_CONTROL_EFFECTIVENESS = {"ineffective", "not_tested"}
_BAD_FRAMEWORK_STATUS = {"not_started", "not_applicable"}


def _substantiator_issue(sub: dict[str, Any]) -> str | None:
    """Return a human-readable reason this substantiator undermines an answer,
    or None if it still supports the claim."""
    status = sub.get("status")
    if status in _DEGRADED_STATUS:
        return f"{sub['name']} is {status}"
    meta = sub.get("metadata") or {}
    if sub["type"] == "control":
        eff = meta.get("effectiveness")
        if eff in _BAD_CONTROL_EFFECTIVENESS:
            return f"control “{sub['name']}” is {eff.replace('_', ' ')}"
    if sub["type"] == "framework":
        cs = meta.get("certification_status")
        if cs in _BAD_FRAMEWORK_STATUS:
            return f"framework “{sub['name']}” is {cs.replace('_', ' ')}"
    return None


def _review_reasons(substantiators: list[dict[str, Any]]) -> list[str]:
    """Why an answer needs review: unsubstantiated, or backed by degraded assets."""
    if not substantiators:
        return ["no substantiating assets"]
    reasons = []
    for s in substantiators:
        issue = _substantiator_issue(s)
        if issue:
            reasons.append(issue)
    return reasons


async def list_answers(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Every Answer asset with the assets that substantiate it.

    Returns one dict per answer: id, question, answer, short_answer,
    last_reviewed, status, and a ``substantiators`` list of {id, name, type}.
    """
    rows = await pool.fetch(
        """SELECT a.id, a.name, a.description, a.status, a.metadata, a.updated_at,
                  s.id AS sub_id, s.name AS sub_name, s.type::text AS sub_type,
                  s.status AS sub_status, s.metadata AS sub_metadata
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
                {
                    "id": r["sub_id"],
                    "name": r["sub_name"],
                    "type": r["sub_type"],
                    "status": r["sub_status"],
                    "metadata": _as_dict(r["sub_metadata"]),
                }
            )

    result = list(by_answer.values())
    for entry in result:
        entry["review_reasons"] = _review_reasons(entry["substantiators"])
        entry["needs_review"] = bool(entry["review_reasons"])
    return result


async def count_needs_review(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> int:
    """How many answers currently need review (stale or unsubstantiated).

    Computed from substantiating-asset state; modest library volume makes the
    full walk cheap enough to avoid a bespoke aggregate query.
    """
    answers = await list_answers(pool, organization_id=organization_id)
    return sum(1 for a in answers if a["needs_review"])


async def count_answers(pool: asyncpg.Pool, *, organization_id: UUID | None = None) -> int:
    return await pool.fetchval(
        """SELECT count(*) FROM assets
           WHERE type = $1 AND ($2::uuid IS NULL OR organization_id = $2)""",
        AssetType.ANSWER.value,
        organization_id,
    )
