"""Evidence freshness engine.

Attachments (a control screenshot, a SOC 2 report, a DPA) carry ``collected_at``
and an optional ``valid_until``. This service classifies each item's freshness so
stale evidence stops silently passing as proof at audit, and it powers
freshness-gated coverage (a requirement isn't truly covered if the control's
evidence has expired). Mirrors the answer_service freshness pattern over
attachment timestamps.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

AGING_DAYS = 30

# worst-first ranking for rolling many attachments up to one status
RANK = {"fresh": 0, "untracked": 1, "aging": 2, "expired": 3}
STALE = ("aging", "expired")


def classify(valid_until: datetime | None, *, now: datetime | None = None) -> str:
    """One of fresh | aging | expired | untracked (no valid_until set)."""
    if valid_until is None:
        return "untracked"
    now = now or datetime.now(UTC)
    if valid_until < now:
        return "expired"
    if valid_until <= now + timedelta(days=AGING_DAYS):
        return "aging"
    return "fresh"


def worst(statuses) -> str | None:
    """The worst (most-stale) of a set of freshness statuses, or None if empty."""
    out: str | None = None
    for st in statuses:
        if out is None or RANK[st] > RANK[out]:
            out = st
    return out


async def evidence_status_for_assets(
    pool: asyncpg.Pool, asset_ids: list[UUID], *, now: datetime | None = None
) -> dict[UUID, str]:
    """{asset_id: worst freshness among its attachments}; assets with no
    attachments are absent from the map."""
    if not asset_ids:
        return {}
    rows = await pool.fetch(
        "SELECT asset_id, valid_until FROM attachments WHERE asset_id = ANY($1::uuid[])",
        asset_ids,
    )
    by_asset: dict[UUID, list[str]] = {}
    for r in rows:
        by_asset.setdefault(r["asset_id"], []).append(classify(r["valid_until"], now=now))
    out: dict[UUID, str] = {}
    for aid, sts in by_asset.items():
        w = worst(sts)
        if w is not None:
            out[aid] = w
    return out


async def list_stale_evidence(
    pool: asyncpg.Pool, *, organization_id: UUID, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Attachments that are aging or expired, soonest-expiring first."""
    now = now or datetime.now(UTC)
    rows = await pool.fetch(
        """SELECT at.id, at.name, at.asset_id, at.valid_until, at.collected_at,
                  a.name AS asset_name, a.type::text AS asset_type
           FROM attachments at JOIN assets a ON a.id = at.asset_id
           WHERE at.organization_id = $1 AND at.valid_until IS NOT NULL
             AND at.valid_until <= $2
           ORDER BY at.valid_until""",
        organization_id, now + timedelta(days=AGING_DAYS),
    )
    return [
        {
            "id": r["id"], "name": r["name"], "asset_id": r["asset_id"],
            "asset_name": r["asset_name"], "asset_type": r["asset_type"],
            "valid_until": r["valid_until"],
            "status": classify(r["valid_until"], now=now),
        }
        for r in rows
    ]


async def expiring_evidence(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID | None = None,
    within_days: int = AGING_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Evidence already expired or expiring within N days (used by the nightly check)."""
    now = now or datetime.now(UTC)
    rows = await pool.fetch(
        """SELECT at.id, at.name, at.asset_id, at.valid_until,
                  a.name AS asset_name, a.owner_id
           FROM attachments at JOIN assets a ON a.id = at.asset_id
           WHERE ($1::uuid IS NULL OR at.organization_id = $1)
             AND at.valid_until IS NOT NULL AND at.valid_until <= $2
           ORDER BY at.valid_until""",
        organization_id, now + timedelta(days=within_days),
    )
    return [dict(r) for r in rows]
