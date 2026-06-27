"""Outbound vendor questionnaire campaigns.

A campaign is a questionnaire WE send to a vendor. The vendor answers through a
login-less portal keyed by an unguessable ``access_token`` — no account needed.
Two tables mirror the inbound questionnaire shape: a header (vendor_campaigns)
plus one row per question (vendor_campaign_questions).

Lifecycle: draft → sent → in_progress → submitted → reviewed. The portal is only
reachable once a campaign is sent; it stays read-only after the vendor submits.
"""
from __future__ import annotations

import secrets
from uuid import UUID

import asyncpg

# Statuses a vendor may reach the portal under. 'draft' is hidden (404) so a
# half-built campaign isn't visible before it's deliberately sent.
PORTAL_VISIBLE = ("sent", "in_progress", "submitted", "reviewed")
# Statuses where the vendor may still edit answers.
PORTAL_EDITABLE = ("sent", "in_progress")


def _new_token() -> str:
    return secrets.token_urlsafe(32)


async def create_campaign(
    pool: asyncpg.Pool, *, organization_id: UUID, name: str,
    vendor_asset_id: UUID | None = None, due_date=None, created_by: UUID | None = None,
) -> asyncpg.Record:
    return await pool.fetchrow(
        """INSERT INTO vendor_campaigns
               (organization_id, name, vendor_asset_id, access_token, due_date, created_by)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
        organization_id, name, vendor_asset_id, _new_token(), due_date, created_by,
    )


async def list_campaigns(pool: asyncpg.Pool, *, organization_id: UUID) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.*, va.name AS vendor_name,
                  count(q.id) AS total,
                  count(q.id) FILTER (WHERE q.status = 'answered') AS answered
           FROM vendor_campaigns c
           LEFT JOIN assets va ON va.id = c.vendor_asset_id
           LEFT JOIN vendor_campaign_questions q ON q.campaign_id = c.id
           WHERE c.organization_id = $1
           GROUP BY c.id, va.name
           ORDER BY c.created_at DESC""",
        organization_id,
    )
    return [dict(r) for r in rows]


async def get_campaign(
    pool: asyncpg.Pool, campaign_id: UUID, *, organization_id: UUID
) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """SELECT c.*, va.name AS vendor_name FROM vendor_campaigns c
           LEFT JOIN assets va ON va.id = c.vendor_asset_id
           WHERE c.id = $1 AND c.organization_id = $2""",
        campaign_id, organization_id,
    )


async def get_by_token(pool: asyncpg.Pool, token: str) -> asyncpg.Record | None:
    """Portal lookup — the token IS the capability, so no org filter."""
    return await pool.fetchrow(
        """SELECT c.*, o.name AS org_name FROM vendor_campaigns c
           JOIN organizations o ON o.id = c.organization_id
           WHERE c.access_token = $1""",
        token,
    )


async def list_questions(pool: asyncpg.Pool, campaign_id: UUID) -> list[asyncpg.Record]:
    return list(await pool.fetch(
        """SELECT * FROM vendor_campaign_questions
           WHERE campaign_id = $1 ORDER BY position, created_at""",
        campaign_id,
    ))


async def _next_position(pool: asyncpg.Pool, campaign_id: UUID) -> int:
    n = await pool.fetchval(
        """SELECT COALESCE(max(position), -1) + 1
           FROM vendor_campaign_questions WHERE campaign_id = $1""",
        campaign_id,
    )
    return int(n or 0)


async def add_question(
    pool: asyncpg.Pool, campaign_id: UUID, *, organization_id: UUID, text: str
) -> None:
    pos = await _next_position(pool, campaign_id)
    await pool.execute(
        """INSERT INTO vendor_campaign_questions
               (campaign_id, organization_id, position, question_text)
           VALUES ($1, $2, $3, $4)""",
        campaign_id, organization_id, pos, text,
    )


async def import_questions(
    pool: asyncpg.Pool, campaign_id: UUID, *, organization_id: UUID, questions: list[str]
) -> int:
    pos = await _next_position(pool, campaign_id)
    added = 0
    for q in questions:
        q = q.strip()
        if not q:
            continue
        await pool.execute(
            """INSERT INTO vendor_campaign_questions
                   (campaign_id, organization_id, position, question_text)
               VALUES ($1, $2, $3, $4)""",
            campaign_id, organization_id, pos, q,
        )
        pos += 1
        added += 1
    return added


async def set_status(
    pool: asyncpg.Pool, campaign_id: UUID, status: str, *, organization_id: UUID
) -> None:
    await pool.execute(
        """UPDATE vendor_campaigns SET status = $1, updated_at = now()
           WHERE id = $2 AND organization_id = $3""",
        status, campaign_id, organization_id,
    )


async def save_answers(pool: asyncpg.Pool, campaign_id: UUID, answers: dict[UUID, str]) -> None:
    """Portal-side bulk save. Campaign already validated by token upstream."""
    async with pool.acquire() as conn, conn.transaction():
        for qid, text in answers.items():
            text = (text or "").strip()
            await conn.execute(
                """UPDATE vendor_campaign_questions
                   SET answer = $1, status = $2
                   WHERE id = $3 AND campaign_id = $4""",
                text, "answered" if text else "unanswered", qid, campaign_id,
            )
        # First answer flips a freshly-sent campaign to in_progress.
        await conn.execute(
            """UPDATE vendor_campaigns SET status = 'in_progress', updated_at = now()
               WHERE id = $1 AND status = 'sent'""",
            campaign_id,
        )


async def submit(pool: asyncpg.Pool, campaign_id: UUID) -> None:
    await pool.execute(
        """UPDATE vendor_campaigns SET status = 'submitted', updated_at = now()
           WHERE id = $1 AND status IN ('sent', 'in_progress')""",
        campaign_id,
    )


def progress(questions: list) -> tuple[int, int]:
    total = len(questions)
    answered = sum(1 for q in questions if q["status"] == "answered")
    return answered, total
