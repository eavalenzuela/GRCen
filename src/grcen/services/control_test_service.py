"""Control test ledger.

Each recorded test of a control is an immutable row in ``control_test_runs``.
The *latest* run writes ``effectiveness`` / ``last_tested`` / ``next_test_due``
back onto the control asset's metadata so the existing risk rollup and
answer-freshness signals keep working unchanged — while the full series powers a
result-history sparkline, an org-wide "overdue for test" list, and a "control
operated continuously over [period]" assurance check (what SOC 2 / ISO actually
require: operated *throughout* a period, not merely passed once).
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import asyncpg

# A test result maps onto the control's existing effectiveness enum, so the
# latest run keeps the risk rollup and answer-freshness current.
RESULT_TO_EFFECTIVENESS = {
    "pass": "effective",
    "partial": "partially_effective",
    "fail": "ineffective",
}
VALID_RESULTS = frozenset(RESULT_TO_EFFECTIVENESS)
VALID_METHODS = frozenset({"manual", "automated", "connector"})

# Testing cadence → days until the next test is due.
_FREQUENCY_DAYS = {
    "continuous": 7,
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "annually": 365,
}


def next_due(frequency: str | None, from_date: date) -> date | None:
    """The next test-due date given a control's frequency, or None if unknown."""
    days = _FREQUENCY_DAYS.get(frequency or "")
    return from_date + timedelta(days=days) if days else None


def _parse_meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


async def record_test_run(
    pool: asyncpg.Pool,
    control_id: UUID,
    *,
    result: str,
    method: str = "manual",
    tested_by: UUID | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    notes: str | None = None,
    evidence_url: str | None = None,
    organization_id: UUID,
) -> dict[str, Any]:
    """Record a control test and roll the result up onto the control asset."""
    if result not in VALID_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_RESULTS)}")
    if method not in VALID_METHODS:
        raise ValueError(f"method must be one of {sorted(VALID_METHODS)}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            ctrl = await conn.fetchrow(
                """SELECT id, metadata FROM assets
                   WHERE id = $1 AND type = 'control' AND organization_id = $2""",
                control_id, organization_id,
            )
            if ctrl is None:
                raise ValueError("control not found")
            run = await conn.fetchrow(
                """INSERT INTO control_test_runs
                       (id, organization_id, control_id, result, method, tested_by,
                        period_start, period_end, notes, evidence_url)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING *""",
                uuid.uuid4(), organization_id, control_id, result, method, tested_by,
                period_start, period_end, notes, evidence_url,
            )
            # Roll the latest result onto the control's effectiveness/last_tested
            # and compute the next due date from its frequency.
            meta = _parse_meta(ctrl["metadata"])
            run_date = run["run_at"].date()
            meta["effectiveness"] = RESULT_TO_EFFECTIVENESS[result]
            meta["last_tested"] = run_date.isoformat()
            nd = next_due(meta.get("frequency"), run_date)
            if nd:
                meta["next_test_due"] = nd.isoformat()
            await conn.execute(
                """UPDATE assets SET metadata = $1::jsonb, updated_at = now(),
                       updated_by = COALESCE($2, updated_by) WHERE id = $3""",
                json.dumps(meta), tested_by, control_id,
            )
    return dict(run)


async def list_test_runs(
    pool: asyncpg.Pool, control_id: UUID, *, organization_id: UUID, limit: int = 100
) -> list[dict[str, Any]]:
    """A control's test history, newest first, with the tester's username."""
    rows = await pool.fetch(
        """SELECT t.*, u.username AS tester_name
           FROM control_test_runs t
           LEFT JOIN users u ON u.id = t.tested_by
           WHERE t.control_id = $1 AND t.organization_id = $2
           ORDER BY t.run_at DESC LIMIT $3""",
        control_id, organization_id, limit,
    )
    return [dict(r) for r in rows]


async def recent_results(
    pool: asyncpg.Pool, control_ids: list[UUID], *, organization_id: UUID, limit: int = 8
) -> dict[UUID, list[str]]:
    """{control_id: [recent results, oldest→newest]} for result-history sparklines."""
    out: dict[UUID, list[str]] = {cid: [] for cid in control_ids}
    if not control_ids:
        return out
    rows = await pool.fetch(
        """SELECT control_id, result FROM control_test_runs
           WHERE control_id = ANY($1::uuid[]) AND organization_id = $2
           ORDER BY run_at DESC""",
        control_ids, organization_id,
    )
    for r in rows:
        bucket = out.setdefault(r["control_id"], [])
        if len(bucket) < limit:
            bucket.append(r["result"])
    return {cid: list(reversed(v)) for cid, v in out.items()}


async def overdue_for_test(
    pool: asyncpg.Pool, *, organization_id: UUID | None = None, today: date | None = None
) -> list[dict[str, Any]]:
    """Active controls past their ``next_test_due`` or never tested."""
    today = today or date.today()
    rows = await pool.fetch(
        """SELECT id, name, metadata FROM assets
           WHERE type = 'control' AND status = 'active'
             AND ($1::uuid IS NULL OR organization_id = $1)
           ORDER BY name""",
        organization_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = _parse_meta(r["metadata"])
        last = meta.get("last_tested")
        nd = meta.get("next_test_due")
        overdue = False
        if not last:
            overdue = True  # never tested
        elif nd:
            try:
                overdue = date.fromisoformat(nd) < today
            except (ValueError, TypeError):
                overdue = False
        if overdue:
            out.append({
                "id": r["id"], "name": r["name"],
                "frequency": meta.get("frequency"),
                "last_tested": last, "next_test_due": nd,
                "effectiveness": meta.get("effectiveness"),
                "never_tested": not last,
            })
    return out


async def operated_continuously(
    pool: asyncpg.Pool,
    control_id: UUID,
    *,
    start: date,
    end: date,
    organization_id: UUID,
) -> bool:
    """True if a passing test's period covers [start, end] (operated throughout)."""
    count = await pool.fetchval(
        """SELECT count(*) FROM control_test_runs
           WHERE control_id = $1 AND organization_id = $2 AND result = 'pass'
             AND period_start IS NOT NULL AND period_end IS NOT NULL
             AND period_start <= $3 AND period_end >= $4""",
        control_id, organization_id, start, end,
    )
    return (count or 0) > 0
