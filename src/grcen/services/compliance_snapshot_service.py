"""Compliance posture snapshots — the compliance analogue of risk_snapshots.

A nightly job writes one row per org/framework/day so GRCen can prove sustained
coverage across an audit period, answer "what was coverage on the audit date?",
chart a coverage timeline, and detect drift when coverage regresses.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from grcen.services import framework_service


async def capture_compliance_snapshot(
    pool: asyncpg.Pool, *, organization_id: UUID, for_date: date | None = None
) -> int:
    """Write today's per-framework coverage row for one org. Idempotent per day."""
    snap_date = for_date or date.today()
    summaries = await framework_service.list_frameworks(
        pool, organization_id=organization_id
    )
    for s in summaries:
        open_gap = s.requirement_count - s.satisfied_count - s.borrowed_count
        await pool.execute(
            """INSERT INTO compliance_snapshots
                   (organization_id, framework_id, snapshot_date, framework_name,
                    requirement_count, satisfied_count, borrowed_count, open_gap_count,
                    coverage_pct, effective_coverage_pct)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (organization_id, framework_id, snapshot_date) DO UPDATE SET
                   framework_name = EXCLUDED.framework_name,
                   requirement_count = EXCLUDED.requirement_count,
                   satisfied_count = EXCLUDED.satisfied_count,
                   borrowed_count = EXCLUDED.borrowed_count,
                   open_gap_count = EXCLUDED.open_gap_count,
                   coverage_pct = EXCLUDED.coverage_pct,
                   effective_coverage_pct = EXCLUDED.effective_coverage_pct,
                   created_at = now()""",
            organization_id, s.id, snap_date, s.name, s.requirement_count,
            s.satisfied_count, s.borrowed_count, open_gap, s.coverage_percent,
            s.effective_coverage_percent,
        )
    return len(summaries)


async def capture_all_org_compliance_snapshots(
    pool: asyncpg.Pool, for_date: date | None = None
) -> int:
    """Run a daily compliance snapshot for every org. Used by the scheduler."""
    orgs = await pool.fetch("SELECT id FROM organizations")
    total = 0
    for o in orgs:
        total += await capture_compliance_snapshot(
            pool, organization_id=o["id"], for_date=for_date
        )
    return total


async def get_coverage_trends(
    pool: asyncpg.Pool, summaries, *, organization_id: UUID
) -> dict[str, dict[str, Any]]:
    """{framework_id: {current, prior, delta, sparkline}} for the index trend arrows.

    ``summaries`` is the already-computed ``list_frameworks`` result so the index
    page doesn't recompute coverage. 'current' is live; 'prior' is the most recent
    snapshot dated before today.
    """
    today = date.today()
    rows = await pool.fetch(
        """SELECT framework_id, snapshot_date, effective_coverage_pct
           FROM compliance_snapshots WHERE organization_id = $1
           ORDER BY framework_id, snapshot_date""",
        organization_id,
    )
    series: dict[UUID, list[tuple[date, int]]] = {}
    for r in rows:
        series.setdefault(r["framework_id"], []).append(
            (r["snapshot_date"], r["effective_coverage_pct"])
        )
    out: dict[str, dict[str, Any]] = {}
    for s in summaries:
        hist = series.get(s.id, [])
        prior = next((v for d, v in reversed(hist) if d < today), None)
        current = s.effective_coverage_percent
        out[str(s.id)] = {
            "current": current,
            "prior": prior,
            "delta": (current - prior) if prior is not None else None,
            "sparkline": [v for _, v in hist][-12:],
        }
    return out


async def get_coverage_timeline(
    pool: asyncpg.Pool, framework_id: UUID, *, organization_id: UUID, limit: int = 90
) -> list[dict[str, Any]]:
    """Coverage series (oldest→newest) for a framework, for the detail-page chart."""
    rows = await pool.fetch(
        """SELECT snapshot_date, coverage_pct, effective_coverage_pct, satisfied_count,
                  borrowed_count, open_gap_count, requirement_count
           FROM compliance_snapshots
           WHERE organization_id = $1 AND framework_id = $2
           ORDER BY snapshot_date DESC LIMIT $3""",
        organization_id, framework_id, limit,
    )
    return [dict(r) for r in reversed(rows)]


async def coverage_drift(
    pool: asyncpg.Pool, *, organization_id: UUID
) -> list[dict[str, Any]]:
    """Frameworks whose effective coverage dropped between the two latest snapshots."""
    rows = await pool.fetch(
        """SELECT framework_id, framework_name, snapshot_date, effective_coverage_pct
           FROM compliance_snapshots WHERE organization_id = $1
           ORDER BY framework_id, snapshot_date DESC""",
        organization_id,
    )
    by_fw: dict[UUID, list[asyncpg.Record]] = {}
    for r in rows:
        by_fw.setdefault(r["framework_id"], []).append(r)
    drift: list[dict[str, Any]] = []
    for fid, snaps in by_fw.items():
        if len(snaps) < 2:
            continue
        latest, prev = snaps[0]["effective_coverage_pct"], snaps[1]["effective_coverage_pct"]
        if latest < prev:
            drift.append({
                "framework_id": str(fid),
                "framework_name": snaps[0]["framework_name"],
                "from_pct": prev,
                "to_pct": latest,
                "date": snaps[0]["snapshot_date"],
            })
    return drift
