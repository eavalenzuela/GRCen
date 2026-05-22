"""Inbound questionnaire import & fill (feature_roadmap.md #21 Phase 3).

A questionnaire is a document a customer/prospect sent us; each row is a
question we answer — ideally by mapping it to an answer-library entry
(AssetType.ANSWER), which auto-fills the canonical answer.
"""
import csv
import io
from typing import Any
from uuid import UUID

import asyncpg

VALID_STATUS = {"draft", "in_progress", "submitted"}


async def create_questionnaire(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    name: str,
    source: str = "",
    due_date: Any = None,
    created_by: UUID | None = None,
) -> UUID:
    return await pool.fetchval(
        """INSERT INTO questionnaires (organization_id, name, source, due_date, created_by)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        organization_id,
        name,
        source,
        due_date,
        created_by,
    )


async def list_questionnaires(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT q.id, q.name, q.source, q.due_date, q.status, q.created_at,
                  count(r.id) AS total,
                  count(r.id) FILTER (WHERE r.status IN ('filled', 'reviewed')) AS answered
           FROM questionnaires q
           LEFT JOIN questionnaire_responses r ON r.questionnaire_id = q.id
           WHERE ($1::uuid IS NULL OR q.organization_id = $1)
           GROUP BY q.id
           ORDER BY q.created_at DESC""",
        organization_id,
    )
    return [dict(r) for r in rows]


async def get_questionnaire(
    pool: asyncpg.Pool, questionnaire_id: UUID, *, organization_id: UUID | None = None
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """SELECT id, name, source, due_date, status, created_at
           FROM questionnaires
           WHERE id = $1 AND ($2::uuid IS NULL OR organization_id = $2)""",
        questionnaire_id,
        organization_id,
    )
    return dict(row) if row else None


async def set_status(
    pool: asyncpg.Pool, questionnaire_id: UUID, status: str, *, organization_id: UUID
) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"Invalid questionnaire status: {status}")
    await pool.execute(
        """UPDATE questionnaires SET status = $1, updated_at = now()
           WHERE id = $2 AND organization_id = $3""",
        status,
        questionnaire_id,
        organization_id,
    )


async def delete_questionnaire(
    pool: asyncpg.Pool, questionnaire_id: UUID, *, organization_id: UUID
) -> bool:
    result = await pool.execute(
        "DELETE FROM questionnaires WHERE id = $1 AND organization_id = $2",
        questionnaire_id,
        organization_id,
    )
    return result.endswith("1")


def parse_questions(content: bytes, *, column: int = 0, has_header: bool = False) -> list[str]:
    """Pull the question text out of an uploaded CSV (one question per row)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if has_header and rows:
        rows = rows[1:]
    questions = []
    for row in rows:
        if column < len(row):
            q = row[column].strip()
            if q:
                questions.append(q)
    return questions


async def import_questions(
    pool: asyncpg.Pool,
    questionnaire_id: UUID,
    questions: list[str],
    *,
    organization_id: UUID,
) -> int:
    """Append questions as response rows. Returns the number inserted."""
    start = await pool.fetchval(
        "SELECT coalesce(max(position), -1) + 1 FROM questionnaire_responses"
        " WHERE questionnaire_id = $1",
        questionnaire_id,
    )
    inserted = 0
    for i, q in enumerate(questions):
        await pool.execute(
            """INSERT INTO questionnaire_responses
                   (questionnaire_id, organization_id, position, question_text)
               VALUES ($1, $2, $3, $4)""",
            questionnaire_id,
            organization_id,
            start + i,
            q,
        )
        inserted += 1
    return inserted


async def list_responses(
    pool: asyncpg.Pool, questionnaire_id: UUID, *, organization_id: UUID | None = None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT r.id, r.position, r.question_text, r.answer_asset_id,
                  r.filled_answer, r.status,
                  a.name AS answer_question
           FROM questionnaire_responses r
           LEFT JOIN assets a ON a.id = r.answer_asset_id
           WHERE r.questionnaire_id = $1
             AND ($2::uuid IS NULL OR r.organization_id = $2)
           ORDER BY r.position""",
        questionnaire_id,
        organization_id,
    )
    return [dict(r) for r in rows]


async def set_response(
    pool: asyncpg.Pool,
    response_id: UUID,
    *,
    organization_id: UUID,
    answer_asset_id: UUID | None = None,
    filled_answer: str | None = None,
    mark_reviewed: bool = False,
) -> None:
    """Map a response to a library answer (auto-filling its canonical text) and/or
    set the filled answer text directly."""
    if answer_asset_id is not None and filled_answer is None:
        # Auto-fill from the mapped answer-library entry's canonical answer.
        filled_answer = await pool.fetchval(
            """SELECT description FROM assets
               WHERE id = $1 AND type = 'answer' AND organization_id = $2""",
            answer_asset_id,
            organization_id,
        ) or ""
    status = "unanswered"
    if mark_reviewed:
        status = "reviewed"
    elif (filled_answer or "").strip() or answer_asset_id is not None:
        status = "filled"
    await pool.execute(
        """UPDATE questionnaire_responses
           SET answer_asset_id = $1, filled_answer = coalesce($2, filled_answer), status = $3
           WHERE id = $4 AND organization_id = $5""",
        answer_asset_id,
        filled_answer,
        status,
        response_id,
        organization_id,
    )
