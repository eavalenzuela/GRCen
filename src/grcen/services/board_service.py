"""Executive board pack: assemble one cross-domain posture snapshot + narratives.

Pulls risk, compliance, incident, finding, appetite and answer-library signals
into a single structure rendered as a branded PDF (pdf_service.render_board_pack)
or previewed at /reports/executive. Per-period narrative blocks let commentary
travel with the data instead of being re-typed each quarter.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from grcen.services import (
    answer_service,
    appetite_service,
    compliance_snapshot_service,
    findings_service,
    framework_service,
    risk_service,
)

SECTIONS = ("overview", "risk", "compliance")


def _parse(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


async def _top_risks(pool: asyncpg.Pool, organization_id: UUID, limit: int = 5) -> list[dict]:
    rows = await pool.fetch(
        """SELECT id, name, metadata FROM assets
           WHERE type = 'risk' AND status = 'active' AND organization_id = $1""",
        organization_id,
    )
    scored = []
    for r in rows:
        meta = _parse(r["metadata"])
        resid = meta.get("residual_risk_score")
        score: int | None = None
        if isinstance(resid, int):
            score = resid
        elif isinstance(resid, str) and resid.strip().isdigit():
            score = int(resid)
        if score is None:
            score = risk_service.compute_risk_score(meta.get("likelihood"), meta.get("impact"))
        scored.append({"name": r["name"], "score": score or 0,
                       "category": meta.get("risk_category") or "—"})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


async def gather(pool: asyncpg.Pool, *, organization_id: UUID) -> dict[str, Any]:
    """All board-pack metrics for an org."""
    risk_summary = await risk_service.get_risk_summary(pool, organization_id=organization_id)
    risk_trend = await risk_service.get_severity_trend(pool, organization_id=organization_id)
    appetite = await appetite_service.breach_summary(pool, organization_id=organization_id)
    top_risks = await _top_risks(pool, organization_id)

    summaries = await framework_service.list_frameworks(pool, organization_id=organization_id)
    trends = await compliance_snapshot_service.get_coverage_trends(
        pool, summaries, organization_id=organization_id)
    frameworks = [
        {
            "name": s.name,
            "coverage": s.coverage_percent,
            "effective": s.effective_coverage_percent,
            "open_gaps": max(0, s.requirement_count - s.satisfied_count - s.borrowed_count),
            "delta": trends.get(str(s.id), {}).get("delta"),
            "certification": s.metadata.get("certification_status"),
        }
        for s in summaries
    ]
    total_open_gaps = sum(
        max(0, s.requirement_count - s.satisfied_count - s.borrowed_count) for s in summaries)

    open_incidents = await pool.fetchval(
        """SELECT count(*) FROM assets
           WHERE type = 'incident' AND status = 'active' AND organization_id = $1
             AND COALESCE(metadata->>'incident_status', 'open')
                 NOT IN ('resolved', 'closed')""",
        organization_id,
    )
    overdue_findings = len(
        await findings_service.overdue_findings(pool, organization_id=organization_id))
    answers = await answer_service.list_answers(pool, organization_id=organization_id)
    answers_needing_review = len([a for a in answers if a.get("needs_review")])

    return {
        "risk": {
            "summary": risk_summary,
            "trend": risk_trend,
            "appetite": appetite,
            "top": top_risks,
        },
        "compliance": {
            "frameworks": frameworks,
            "total_open_gaps": total_open_gaps,
        },
        "operations": {
            "open_incidents": open_incidents or 0,
            "overdue_findings": overdue_findings,
            "answers_needing_review": answers_needing_review,
        },
    }


async def get_narratives(
    pool: asyncpg.Pool, *, organization_id: UUID, period: str
) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT section, body FROM board_narratives WHERE organization_id = $1 AND period = $2",
        organization_id, period,
    )
    return {r["section"]: r["body"] for r in rows}


async def set_narrative(
    pool: asyncpg.Pool, *, organization_id: UUID, period: str, section: str, body: str
) -> None:
    await pool.execute(
        """INSERT INTO board_narratives (organization_id, period, section, body)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (organization_id, period, section) DO UPDATE SET
               body = EXCLUDED.body, updated_at = now()""",
        organization_id, period, section, body,
    )
